#!/bin/bash
# Aplica as migrations de /migrations (uma vez cada, registradas em app_internal.migrations)
# e depois os scripts idempotentes de /post. Roda a cada "docker compose up".
set -euo pipefail
PSQL=(psql -v ON_ERROR_STOP=1 --no-psqlrc -q)

echo "[migrate] aguardando o schema de autenticação (auth.uid)..."
for i in $(seq 1 60); do
  if [ "$("${PSQL[@]}" -tAc "select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='auth' and p.proname='uid'")" = "1" ]; then break; fi
  [ "$i" = 60 ] && { echo "[migrate] ERRO: auth.uid() não apareceu"; exit 1; }
  sleep 2
done

"${PSQL[@]}" -c "create schema if not exists app_internal; create table if not exists app_internal.migrations (name text primary key, applied_at timestamptz not null default now());"

for f in $(ls /migrations/*.sql | sort); do
  n=$(basename "$f")
  if [ "$("${PSQL[@]}" -tAc "select 1 from app_internal.migrations where name='$n'")" = "1" ]; then
    echo "[migrate] já aplicada: $n"; continue
  fi
  echo "[migrate] aplicando: $n"
  "${PSQL[@]}" --single-transaction -f "$f" -c "insert into app_internal.migrations(name) values ('$n')"
done

for f in $(ls /post/*.sql | sort); do
  echo "[migrate] pós-configuração: $(basename "$f")"
  "${PSQL[@]}" -f "$f"
done

"${PSQL[@]}" -c "notify pgrst, 'reload schema'"
echo "[migrate] concluído."
