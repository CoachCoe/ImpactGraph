-- name: RefreshRankings :exec
-- Recompute the whole ranking population in one set-based pass (PRD §13 risk #6:
-- periodic, never per request). The eligible population is donors with a current
-- streak who have not opted out; ties break on the earlier streak start, so a
-- longer-standing identical streak ranks higher. national_percentile is the
-- top-percentile bucket (1 = top 1%); city_rank is 1-based within donor.city.
-- Replaces the prior snapshot wholesale.
WITH eligible AS (
  SELECT d.id AS donor_id, d.city AS city,
         (sqlc.arg(computed_at)::timestamptz AT TIME ZONE coalesce(d.timezone,'America/New_York'))::date
           - (p.streak_since AT TIME ZONE coalesce(d.timezone,'America/New_York'))::date AS streak,
         p.streak_since AS since
  FROM donors d
  JOIN donor_metric_projections p ON p.donor_id = d.id
  LEFT JOIN donor_preferences pref ON pref.donor_id = d.id
  WHERE d.disabled = false
    AND p.current_streak IS NOT NULL
    AND p.current_streak > 0
    AND p.streak_since IS NOT NULL
    AND p.next_refresh_at > sqlc.arg(computed_at)::timestamptz
    AND COALESCE(pref.rankings_opted_out, false) = false
),
ranked AS (
  SELECT donor_id, city, since,
         -- percent_rank is 0 for the top; map to a top-percentile 1..100
         -- (1 = best). A single-row population is the top, percentile 1.
         GREATEST(1, LEAST(100,
           CEIL(PERCENT_RANK() OVER (ORDER BY streak DESC, since ASC) * 100)::int
         )) AS national_percentile,
         CASE WHEN city IS NOT NULL
              THEN RANK() OVER (PARTITION BY city ORDER BY streak DESC, since ASC)::int
         END AS city_rank
  FROM eligible
)
INSERT INTO donor_rankings (donor_id, national_percentile, city, city_rank, computed_at)
SELECT donor_id, national_percentile, city, city_rank, sqlc.arg(computed_at)::timestamptz
FROM ranked
ON CONFLICT (donor_id) DO UPDATE SET
  national_percentile = EXCLUDED.national_percentile,
  city = EXCLUDED.city,
  city_rank = EXCLUDED.city_rank,
  computed_at = EXCLUDED.computed_at;

-- name: PruneStaleRankings :exec
-- Remove rows for donors no longer in the eligible population (opted out,
-- lapsed, disabled) as of the given refresh time.
DELETE FROM donor_rankings WHERE computed_at < $1;

-- name: DonorRanking :one
-- A donor's own ranking row (K-3: only ever their own position).
SELECT r.national_percentile, r.city, r.city_rank, r.computed_at
FROM donor_rankings r
JOIN donors d ON d.id=r.donor_id
JOIN donor_metric_projections p ON p.donor_id=r.donor_id
LEFT JOIN donor_preferences pref ON pref.donor_id=r.donor_id
WHERE r.donor_id=$1 AND NOT d.disabled AND NOT coalesce(pref.rankings_opted_out,false)
  AND p.current_streak>0 AND p.streak_since IS NOT NULL AND p.next_refresh_at>clock_timestamp();

-- name: ClaimProjectionSchedule :execrows
INSERT INTO donor_projection_schedules(id,next_run_at) VALUES($1,$2)
ON CONFLICT(id) DO UPDATE SET next_run_at=EXCLUDED.next_run_at
WHERE donor_projection_schedules.next_run_at <= sqlc.arg(as_of)::timestamptz;

-- name: SaveProjectionSchedule :exec
INSERT INTO donor_projection_schedules(id,next_run_at) VALUES($1,$2)
ON CONFLICT(id) DO UPDATE SET next_run_at=EXCLUDED.next_run_at;

-- name: AwardRankingBadges :exec
-- Top 2% and Top 50 City for every donor the rollup just ranked into them,
-- queueing the badge-earned push for each new one, in one pass.
WITH qualifying AS (
  SELECT donor_id, 'NAT2'::varchar AS code, NULL::varchar AS city FROM donor_rankings
  WHERE computed_at = sqlc.arg(computed_at)::timestamptz AND national_percentile <= coalesce((SELECT threshold FROM donor_badge_rules WHERE code='NAT2'),2)
  UNION ALL
  SELECT donor_id, 'CITY50'::varchar, city::varchar FROM donor_rankings
  WHERE computed_at = sqlc.arg(computed_at)::timestamptz AND city_rank <= coalesce((SELECT threshold FROM donor_badge_rules WHERE code='CITY50'),50)
), earned AS (
  INSERT INTO donor_badges (donor_id, code, source, earned_at, city)
  SELECT donor_id, code, 'AUTO', sqlc.arg(computed_at)::timestamptz, city FROM qualifying
  ON CONFLICT (donor_id, code) DO NOTHING
  RETURNING donor_id, code
)
INSERT INTO donor_push_notifications (id, donor_id, category, dedup_key, context, available_at, expires_at)
SELECT gen_random_uuid(), e.donor_id, 'BADGE_EARNED', 'push:badge:' || e.donor_id::text || ':' || e.code,
  json_build_object('code', e.code), sqlc.arg(computed_at)::timestamptz, sqlc.arg(expires_at)::timestamptz
FROM earned e
WHERE EXISTS (SELECT 1 FROM donor_sessions s WHERE s.donor_id = e.donor_id AND s.push_token IS NOT NULL
  AND coalesce(s.refresh_expires_at, s.expires_at) > sqlc.arg(computed_at)::timestamptz)
ON CONFLICT (dedup_key) DO NOTHING;
