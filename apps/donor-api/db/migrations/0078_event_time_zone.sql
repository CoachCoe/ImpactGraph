-- An event's wall-clock time is the venue's, not the viewer's. starts_at and
-- ends_at are instants; time_zone is the IANA zone a client formats them in so
-- a donor travelling, or an app running on another device zone, still shows the
-- time printed at the venue (PRD E-3).
--
-- Existing events take America/New_York, the zone of the launch region; staff
-- can correct any event in the console, and a new event with no zone entered
-- takes the zone of its ZIP.
ALTER TABLE donor_events ADD COLUMN time_zone VARCHAR(64) NOT NULL DEFAULT 'America/New_York';
ALTER TABLE donor_events ADD CONSTRAINT ck_donor_events_time_zone CHECK (time_zone ~ '^[A-Za-z0-9+_/-]+$');
