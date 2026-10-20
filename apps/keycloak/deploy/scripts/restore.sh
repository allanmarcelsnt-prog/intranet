#!/usr/bin/env bash
# Restaura um backup do banco do Keycloak. SUBSTITUI os dados atuais.
# Uso: ./deploy/scripts/restore.sh /mnt/<pool>/backups/keycloak/keycloak-AAAAMMDD-HHMMSS.dump
# Restaure numa instalação da mesma versão do Keycloak que gerou o backup.
set -euo pipefail
cd "$(dirname "$0")/../.."

ARQ="${1:?uso: restore.sh <arquivo.dump>}"
[ -f "$ARQ" ] || { echo "Arquivo não encontrado: $ARQ" >&2; exit 1; }

read -r -p "Isto substitui TODOS os dados do Keycloak por este backup. Digite SIM para continuar: " OK
[ "$OK" = "SIM" ] || { echo "Cancelado."; exit 1; }

docker compose stop keycloak
docker compose exec -T db pg_restore -U keycloak -d keycloak --clean --if-exists --no-owner < "$ARQ"
docker compose start keycloak
echo "Restauração concluída."
