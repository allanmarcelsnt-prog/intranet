#!/usr/bin/env bash
# Promove um operador já cadastrado (tela "Novo operador? Cadastre-se") a administrador aprovado.
# Uso: ./deploy/scripts/make-admin.sh email@safenettecnologia.com.br
set -euo pipefail
cd "$(dirname "$0")/../.."
[ $# -eq 1 ] || { echo "Uso: $0 <email>"; exit 1; }
EMAIL="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
[[ "$EMAIL" =~ ^[a-z0-9._%+-]+@[a-z0-9.-]+$ ]] || { echo "E-mail inválido: $1"; exit 1; }

psql_db() { docker compose exec -T db bash -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -h localhost -U supabase_admin -d postgres -v ON_ERROR_STOP=1 "$@"' _ "$@"; }

FOUND="$(psql_db -tAc "SELECT count(*) FROM public.profiles WHERE lower(email) = '$EMAIL'")"
if [ "${FOUND//[[:space:]]/}" = "0" ]; then
  echo "Nenhum operador cadastrado com o e-mail $EMAIL. Cadastre-se primeiro pela tela de login e rode o script de novo."
  exit 1
fi

psql_db <<SQL
INSERT INTO public.operator_access (user_id, status)
  SELECT id, 'approved' FROM public.profiles WHERE lower(email) = '$EMAIL'
  ON CONFLICT (user_id) DO UPDATE SET status = 'approved', updated_at = now();
INSERT INTO public.user_roles (user_id, role)
  SELECT id, 'admin' FROM public.profiles WHERE lower(email) = '$EMAIL'
  ON CONFLICT DO NOTHING;
SELECT p.name, p.email, o.status, string_agg(r.role::text, ',') AS papeis
  FROM public.profiles p
  JOIN public.operator_access o ON o.user_id = p.id
  JOIN public.user_roles r ON r.user_id = p.id
  WHERE lower(p.email) = '$EMAIL' GROUP BY p.name, p.email, o.status;
SQL
