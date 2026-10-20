#!/usr/bin/env bash
# Gera o .env do Keycloak com segredos aleatórios. Rode UMA vez, no servidor.
# Uso: ./deploy/setup.sh http://192.168.0.171:8180
set -euo pipefail
cd "$(dirname "$0")/.."

URL="${1:-http://192.168.0.171:8180}"

if [ -f .env ]; then
  echo "Já existe um .env aqui. Não vou sobrescrever." >&2
  echo "(Trocar KC_DB_PASSWORD depois da primeira subida NÃO muda a senha dentro do banco.)" >&2
  exit 1
fi

rand() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-28; }

umask 077
cat > .env <<EOF
# Gerado por deploy/setup.sh em $(date '+%Y-%m-%d %H:%M'). NÃO versionar.
KEYCLOAK_URL=${URL}
HTTP_PORT=8180
HTTPS_PORT=8543
KC_DB_PASSWORD=$(rand)
KC_BOOTSTRAP_ADMIN_USERNAME=admin
KC_BOOTSTRAP_ADMIN_PASSWORD=$(rand)
EOF
chmod 600 .env

echo ".env criado (permissão 600). KEYCLOAK_URL=${URL}"
echo "Guarde uma cópia do .env em local seguro (cofre de senhas), fora do repositório."
echo "Próximo passo: docker compose up -d"
