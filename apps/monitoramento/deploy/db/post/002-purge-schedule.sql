-- Limpeza diária dos registros arquivados vencidos (03:00 UTC), equivalente a supabase/archive-cleanup-schedule.sql.
DO $$ BEGIN
  CREATE EXTENSION IF NOT EXISTS pg_cron;
EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'pg_cron indisponível (%). A purga automática NÃO foi agendada; rode SELECT public.purge_expired_archives() manualmente.', SQLERRM;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.schedule('safenet-purge-archived-records', '0 3 * * *', 'SELECT public.purge_expired_archives();');
  END IF;
END $$;
