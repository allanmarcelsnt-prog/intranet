#!/usr/bin/env bash
# Gera o .env com segredos aleatórios e as chaves JWT (anon / service_role) do stack.
# Uso: ./deploy/setup.sh [SITE_URL]     ex.: ./deploy/setup.sh http://192.168.1.50:8080
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env já existe — nada foi alterado. (Apague-o apenas se quiser gerar tudo de novo: isso invalida logins e o banco existente.)"
  exit 0
fi
command -v openssl >/dev/null || { echo "openssl é necessário."; exit 1; }

SITE_URL="${1:-http://localhost:8080}"
POSTGRES_PASSWORD="$(openssl rand -hex 24)"
JWT_SECRET="$(openssl rand -hex 32)"

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
iat="$(date +%s)"
exp="$((iat + 315360000))"   # 10 anos
make_jwt() {
  local header payload sig
  header="$(printf '{"alg":"HS256","typ":"JWT"}' | b64url)"
  payload="$(printf '{"role":"%s","iss":"supabase","iat":%s,"exp":%s}' "$1" "$iat" "$exp" | b64url)"
  sig="$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -hmac "$JWT_SECRET" -binary | b64url)"
  printf '%s.%s.%s' "$header" "$payload" "$sig"
}

umask 077
cat > .env <<ENV
SITE_URL=${SITE_URL}
HTTP_PORT=8080
HTTPS_PORT=8443
NGINX_CONF=nginx.http.conf
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
JWT_SECRET=${JWT_SECRET}
ANON_KEY=$(make_jwt anon)
SERVICE_ROLE_KEY=$(make_jwt service_role)
ENV
echo ".env criado (permissão 600). SITE_URL=${SITE_URL}"
echo "Guarde uma cópia segura do .env: sem ele não é possível reabrir o banco nem validar os logins existentes."
