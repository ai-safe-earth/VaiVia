-- GTFS stops learn WHEN their service runs.
--
-- The 17 stops that are starts claimed a year-round service nobody had
-- checked: load/gtfs.py counted stop_times and never opened calendar.txt,
-- so n_trips was calendar-blind (the gap docs/route-document.md's start/end
-- contract §2 names). The loader now reads calendar.txt and
-- calendar_dates.txt and stores each stop's service span; NULL means the
-- feed carried no calendar for its trips — "unverified", which must never
-- read as "year-round".

ALTER TABLE staging.gtfs_stop ADD COLUMN IF NOT EXISTS service_start date;
ALTER TABLE staging.gtfs_stop ADD COLUMN IF NOT EXISTS service_end date;
