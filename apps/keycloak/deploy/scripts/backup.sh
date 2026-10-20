#!/usr/bin/env bash
# Backup do banco do Keycloak (usuários locais, grupos, clientes e configurações).
# Uso: ./deploy/scripts/backup.sh /mnt/<pool>/backups/keycloak 14   (pasta, dias de retenção)
# Guarde também uma cópia do .env (sem ele não há como reconectar o banco).
set -euo pipefail
cd "$(dirname "$0")/../.."

DEST="${1:?uso: backup.sh <pasta-destino> [dias-de-retencao]}"
DIAS="${2:-14}"
mkdir -p "$DEST"

ARQ="$DEST/keycloak-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db pg_dump -U keycloak -d keycloak -Fc > "$ARQ"
chmod 600 "$ARQ"

# Remove só os backups deste script mais antigos que a retenção.
find "$DEST" -maxdepth 1 -name 'keycloak-*.dump' -mtime +"$DIAS" -delete

echo "Backup gerado: $ARQ"
