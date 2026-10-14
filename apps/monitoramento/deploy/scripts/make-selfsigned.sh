#!/usr/bin/env bash
# Gera um certificado autoassinado (365 dias) para o gateway HTTPS. Aceita nome DNS ou IP.
# ATENÇÃO: para "Escolher pasta" funcionar, o certificado precisa ser CONFIÁVEL nos PCs dos operadores
# (instale deploy/certs/fullchain.pem como autoridade confiável) — ou use um certificado da CA da empresa.
# Uso: ./deploy/scripts/make-selfsigned.sh monitoramento.safenet.local   (ou 192.168.1.50)
set -euo pipefail
cd "$(dirname "$0")/../.."
[ $# -eq 1 ] || { echo "Uso: $0 <nome-dns-ou-ip>"; exit 1; }
HOST="$1"
if [[ "$HOST" =~ ^[0-9.]+$ ]]; then SAN="IP:$HOST"; else SAN="DNS:$HOST"; fi
mkdir -p deploy/certs
openssl req -x509 -newkey rsa:2048 -nodes -days 365 -sha256 \
  -keyout deploy/certs/privkey.pem -out deploy/certs/fullchain.pem \
  -subj "/CN=$HOST/O=Safenet Tecnologia" -addext "subjectAltName=$SAN" \
  -addext "basicConstraints=CA:FALSE" -addext "keyUsage=digitalSignature,keyEncipherment" -addext "extendedKeyUsage=serverAuth" 2>/dev/null
chmod 600 deploy/certs/privkey.pem
echo "Certificado gerado em deploy/certs/. Ajuste no .env: NGINX_CONF=nginx.https.conf e SITE_URL=https://$HOST:<porta>"
