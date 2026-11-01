-- The city a Top 50 City badge was earned in (PRD Appendix A shows the badge as
-- "Top 50 Chicago"). The badge code CITY50 stays the stable identifier; the city
-- is recorded when the badge is earned so it keeps that city if the donor later
-- moves. NULL for every badge that is not a city badge, and for a city badge
-- granted by staff, which is not tied to a ranked city.
--
-- Backfill assumption: an existing CITY50 row was earned from the ranking of the
-- donor's city at the time, and the only city we hold for the donor now is their
-- current one, so it takes donors.city. A donor who moved before this migration
-- shows the new city; no earlier city is recoverable.
ALTER TABLE donor_badges ADD COLUMN city VARCHAR(80);
UPDATE donor_badges b SET city = d.city
FROM donors d
WHERE d.id = b.donor_id AND b.code = 'CITY50' AND b.source = 'AUTO' AND d.city IS NOT NULL;
ALTER TABLE donor_badges ADD CONSTRAINT ck_donor_badges_city CHECK (city IS NULL OR code = 'CITY50');
