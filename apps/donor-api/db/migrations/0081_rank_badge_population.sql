-- A rank among a handful of donors is not an achievement: everyone in a city of
-- twelve is in its top 50. Each ranking records the size of the population it
-- was taken in, and each badge rule carries the smallest population that may
-- award it. The rank badges start at 100; the rank lines donors see are not
-- gated.
ALTER TABLE donor_rankings
  ADD COLUMN national_population INTEGER NOT NULL DEFAULT 0 CHECK (national_population >= 0),
  ADD COLUMN city_population INTEGER NOT NULL DEFAULT 0 CHECK (city_population >= 0);

ALTER TABLE donor_badge_rules
  ADD COLUMN min_population BIGINT NOT NULL DEFAULT 1 CHECK (min_population BETWEEN 1 AND 1000000);
UPDATE donor_badge_rules SET min_population = 100 WHERE code IN ('NAT2', 'CITY50');
