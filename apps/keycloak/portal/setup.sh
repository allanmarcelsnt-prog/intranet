#!/usr/bin/env bash
# Gera o .env do portal. Rode uma vez.  Uso: ./setup.sh <PORTAL_URL> <PORTAL_PORT> <KEYCLOAK_URL> [UPSTREAM]
# Ex. (teste): ./setup.sh http://192.168.0.171:8082 8082 http://192.168.0.171:8190
# Depois edite o .env (nano .env) e cole INTRANET_CLIENT_SECRET (client "intranet" no Keycloak).
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && { echo "Já existe um .env. Não vou sobrescrever." >&2; exit 1; }
PORTAL_URL="${1:?PORTAL_URL}"; PORTAL_PORT="${2:?PORTAL_PORT}"; KC="${3:?KEYCLOAK_URL}"; UP="${4:-http://backend:80}"
SECURE=false; case "$PORTAL_URL" in https://*) SECURE=true;; esac
umask 077
cat > .env <<ENV
PORTAL_PORT=${PORTAL_PORT}
PORTAL_URL=${PORTAL_URL}
KEYCLOAK_URL=${KC}
UPSTREAM=${UP}
COOKIE_SECURE=${SECURE}
COOKIE_SECRET=$(openssl rand -base64 32 | tr '+/' '-_')
INTRANET_CLIENT_SECRET=
ENV
chmod 600 .env
echo ".env criado. Falta colar INTRANET_CLIENT_SECRET (nano .env)."
