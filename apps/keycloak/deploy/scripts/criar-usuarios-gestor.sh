#!/usr/bin/env bash
# Cria no Keycloak as contas dos usuários do gestor de pregão (grupo do portal: intranet).
# Uso (na pasta apps/keycloak):  ./deploy/scripts/criar-usuarios-gestor.sh
# - pede a senha do admin uma vez (não fica gravada);
# - imprime a senha temporária de cada conta NA TELA: guarde no cofre de senhas;
# - quem já existe é pulado; nada é gravado em arquivo.
set -u
cd "$(dirname "$0")/../.."
read -r -s -p "Senha do admin do Keycloak (${KC_ADMIN_USER:-abarros}): " KC_ADMIN_PASSWORD; echo
export KC_ADMIN_USER="${KC_ADMIN_USER:-abarros}" KC_ADMIN_PASSWORD

USUARIOS=(
  "bruno.teixeira|Bruno Teixeira"
  "thiago.dames|Thiago Dames"
  "luana.mota|Luana Mota"
  "adriano.savio|Adriano Sávio"
  "osvair.torraca|Osvair Torraca"
  "rodrigo.iannibelli|Rodrigo Iannibelli"
  "rafael.pires|Rafael Pires"
  "pedro.mendes|Pedro Mendes"
  "jorge.silva|Jorge Silva"
  "bianca.dames|Bianca Dames"
  "rafaela.cerqueira|Rafaela Cerqueira"
  "rafaela.martinho|Rafaela Martinho"
)
for item in "${USUARIOS[@]}"; do
  login="${item%%|*}"; nome="${item#*|}"
  echo "--- $login"
  python3 deploy/scripts/kc.py usuario "$login" --email "$login@safenettecnologia.com.br" \
    --nome "$nome" --grupo intranet || echo "    (pulado/erro acima)"
done
echo; python3 deploy/scripts/kc.py listar
