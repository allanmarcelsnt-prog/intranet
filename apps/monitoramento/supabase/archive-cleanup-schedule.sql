-- Run after applying migrations on the company intranet database.
-- Requires pg_cron. One daily check, at 03:00 UTC, up to 24 hours after expiry.
SELECT cron.schedule('safenet-purge-archived-records', '0 3 * * *',
  $$SELECT public.purge_expired_archives();$$);