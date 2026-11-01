package store

import (
	"context"
	"errors"
	"time"

	"dad/donor-api/internal/store/queries"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"
)

// RankingService owns the periodic ranking rollup (PRD §6.8). Refresh
// recomputes the whole population in one set-based pass on a schedule; the donor
// read is a single-row lookup. This keeps ranking off the per-request path at 1M
// donors (risk #6).
type RankingService struct{ DB *pgxpool.Pool }

// Ranking is a donor's own position (K-3: never anyone else's, never a list).
type Ranking struct {
	NationalPercentile int       `json:"national_percentile"`
	City               *string   `json:"city"`
	CityRank           *int      `json:"city_rank"`
	ComputedAt         time.Time `json:"computed_at"`
	nationalPopulation int
	cityPopulation     int
}

// Refresh recomputes every eligible donor's rank, prunes donors who dropped out
// of the population (opted out, lapsed, disabled) since the last run, and
// awards the ranking badges. It runs in one transaction so readers never see a
// half-rebuilt snapshot.
func (s RankingService) Refresh(ctx context.Context, now time.Time) error {
	return pgx.BeginFunc(ctx, s.DB, func(tx pgx.Tx) error {
		q := queries.New(tx)
		if err := q.SaveProjectionSchedule(ctx, queries.SaveProjectionScheduleParams{ID: "rankings", NextRunAt: timestamp(now.Add(RankingInterval))}); err != nil {
			return err
		}
		return refreshRankings(ctx, q, now)
	})
}

// ForDonor returns a donor's own ranking, or (nil, nil) when they are unranked
// (no streak, opted out, or not yet computed).
func ForDonor(ctx context.Context, q *queries.Queries, donorID pgtype.UUID) (*Ranking, error) {
	row, err := q.DonorRanking(ctx, donorID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	r := &Ranking{
		NationalPercentile: int(row.NationalPercentile),
		ComputedAt:         row.ComputedAt.Time,
		nationalPopulation: int(row.NationalPopulation),
		cityPopulation:     int(row.CityPopulation),
	}
	if row.City.Valid {
		city := row.City.String
		r.City = &city
	}
	if row.CityRank.Valid {
		rank := int(row.CityRank.Int32)
		r.CityRank = &rank
	}
	return r, nil
}

// RankingInterval is how often the global ranking rollup is recomputed. Ranking
// is a periodic projection, not a per-request or per-tick computation (risk #6),
// so the projections worker refreshes it at most this often.
const RankingInterval = 15 * time.Minute

// RefreshIfDue recomputes the rankings only when the last computation is older
// than RankingInterval, so it rides the projections worker loop cheaply. It
// returns whether it refreshed.
func (s RankingService) RefreshIfDue(ctx context.Context, now time.Time) (bool, error) {
	return transact(ctx, s.DB, pgx.TxOptions{}, func(tx pgx.Tx) (bool, error) {
		q := queries.New(tx)
		claimed, err := q.ClaimProjectionSchedule(
			ctx,
			queries.ClaimProjectionScheduleParams{ID: "rankings", NextRunAt: timestamp(now.Add(RankingInterval)), AsOf: timestamp(now)},
		)
		if err != nil || claimed == 0 {
			return false, err
		}
		return true, refreshRankings(ctx, q, now)
	})
}

func refreshRankings(ctx context.Context, q *queries.Queries, now time.Time) error {
	if err := q.RefreshRankings(ctx, timestamp(now)); err != nil {
		return err
	}
	if err := q.PruneStaleRankings(ctx, timestamp(now)); err != nil {
		return err
	}
	return q.AwardRankingBadges(
		ctx,
		queries.AwardRankingBadgesParams{ComputedAt: timestamp(now), ExpiresAt: timestamp(now.Add(pushLifetime))},
	)
}
