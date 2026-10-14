#!/usr/bin/env bash
# Restaura um backup (.dump gerado por backup.sh) SUBSTITUINDO os dados atuais de public e auth.
# A restauração é atômica (uma transação): se algo falhar, o banco continua como estava.
# Uso: ./deploy/scripts/restore.sh caminho/do/backup.dump [--yes]
# Em um servidor novo: suba o stack (docker compose up -d), copie o MESMO .env do servidor antigo e então restaure.
# Restaure sempre numa instalação da mesma versão do stack que gerou o backup.
set -euo pipefail
cd "$(dirname "$0")/../.."
FILE="${1:-}"; [ -f "$FILE" ] || { echo "Uso: $0 <backup.dump> [--yes]"; exit 1; }
if [ "${2:-}" != "--yes" ]; then
  read -r -p "Isto SUBSTITUI todos os dados atuais por '$FILE'. Digite RESTAURAR para continuar: " ok
  [ "$ok" = "RESTAURAR" ] || { echo "Cancelado."; exit 1; }
fi

restart() { docker compose start auth rest app gateway >/dev/null; }
trap restart EXIT

echo "Parando serviços que escrevem no banco..."
docker compose stop gateway app rest auth >/dev/null
docker compose cp "$FILE" db:/tmp/restore.dump

docker compose exec -T db bash -s <<'EOS'
set -euo pipefail
export PGPASSWORD="$POSTGRES_PASSWORD"
trap 'rm -f /tmp/restore.dump /tmp/restore.list' EXIT
# O controle de migrations do GoTrue (auth.schema_migrations) pertence à versão instalada: não é restaurado.
pg_restore -l /tmp/restore.dump | grep -v 'TABLE DATA auth schema_migrations' > /tmp/restore.list
{
  echo "SET session_replication_role = replica;"
  cat <<'SQL'
DO $$ DECLARE r record; BEGIN
  FOR r IN SELECT schemaname, tablename FROM pg_tables
           WHERE schemaname IN ('public','auth') AND NOT (schemaname = 'auth' AND tablename = 'schema_migrations')
  LOOP
    EXECUTE format('TRUNCATE TABLE %I.%I RESTART IDENTITY CASCADE', r.schemaname, r.tablename);
  END LOOP;
END $$;
SQL
  pg_restore --data-only -L /tmp/restore.list -f - /tmp/restore.dump
} | psql -h localhost -U supabase_admin -d postgres -1 -q -v ON_ERROR_STOP=1
EOS

echo "Restauração concluída. Reiniciando serviços..."
