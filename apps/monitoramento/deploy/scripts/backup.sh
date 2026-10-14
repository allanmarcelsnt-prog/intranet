#!/usr/bin/env bash
# Backup completo (dados + contas de login) do banco, em formato custom do pg_dump.
# Uso: ./deploy/scripts/backup.sh [pasta-destino] [dias-de-retenção]
# Agende no TrueNAS (Tarefas > Cron Jobs), por exemplo todo dia às 02:00.
set -euo pipefail
cd "$(dirname "$0")/../.."
DEST="${1:-./backups}"; KEEP="${2:-14}"
mkdir -p "$DEST"
FILE="$DEST/safenet-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db bash -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -h localhost -U supabase_admin -d postgres -Fc -n public -n auth' > "$FILE"
[ -s "$FILE" ] || { echo "Backup vazio — algo deu errado."; rm -f "$FILE"; exit 1; }
find "$DEST" -name 'safenet-*.dump' -mtime +"$KEEP" -delete
echo "Backup criado: $FILE ($(du -h "$FILE" | cut -f1))"
