"""
migrar_fatia1_rh.py — Gestor de RH · Fatia 1 · Migração única
==============================================================
Execute UMA VEZ no TrueNAS após copiar os arquivos da Fatia 1.

O que este script faz:
  1. Adiciona papéis RH nos usuários existentes (campo 'papeis_extras').
  2. Cria os usuários Rafaela Cerqueira e Rafaela Martinho (sem senha).
  3. Verifica e imprime o resultado para conferência.

Seguro de rodar mais de uma vez — todas as operações são idempotentes.
Não altera o campo 'papel' de nenhum usuário existente.

Uso:
  cd /mnt/Safenet_Storage/intranet
  python migrar_fatia1_rh.py

IMPORTANTE: substitua os e-mails das Rafaelas antes de rodar.
"""
import sys
from pathlib import Path

# Garante que importamos do diretório certo
sys.path.insert(0, str(Path(__file__).parent))
import auth_store

# ---------------------------------------------------------------------------
# 1. Papéis RH dos usuários existentes
#    login  →  papel_rh a adicionar
# ---------------------------------------------------------------------------
PAPEIS_A_ADICIONAR = [
    ("pmendes",     "rh_coordenador"),   # Pedro Mendes — coordenador de RH
    ("riannibelli", "rh_tecnico"),        # Rodrigo Iannibelli — técnico/seleção
    ("bteixeira",   "rh_solicitante"),    # Bruno Teixeira — pode abrir vagas
    ("asavio",      "rh_solicitante"),    # Adriano Sávio — pode abrir vagas
    ("allan",       "rh_solicitante"),    # Allan Marcel — já é coordenador (herança total)
]

# ---------------------------------------------------------------------------
# 2. Novos usuários a criar
#    !! SUBSTITUA OS E-MAILS ANTES DE RODAR !!
# ---------------------------------------------------------------------------
NOVOS_USUARIOS = [
    {
        "nome":    "Rafaela Cerqueira",
        "usuario": "rcerqueira",
        "papel":   "operacional",         # papel principal (Reggra-compatível)
        "email":   "rafaela.cerqueira@safenettecnologia.com.br",  # ← ALTERAR
        "papel_rh": "rh_operador",
    },
    {
        "nome":    "Rafaela Martinho",
        "usuario": "rmartinho",
        "papel":   "operacional",         # papel principal (Reggra-compatível)
        "email":   "rafaela.martinho@safenettecnologia.com.br", # ← ALTERAR
        "papel_rh": "rh_operador",
    },
]

# ---------------------------------------------------------------------------
# Validação prévia
# ---------------------------------------------------------------------------
emails_pendentes = [u for u in NOVOS_USUARIOS if "SUBSTITUIR" in u["email"]]
if emails_pendentes:
    print("=" * 60)
    print("ATENÇÃO: substitua os e-mails antes de rodar este script.")
    for u in emails_pendentes:
        print(f"  → {u['nome']}: {u['email']}")
    print("=" * 60)
    sys.exit(1)

print()
print("=" * 60)
print("  Migração Fatia 1 — Gestor de RH")
print("=" * 60)

# ---------------------------------------------------------------------------
# Passo 1: adicionar papéis RH nos existentes
# ---------------------------------------------------------------------------
print("\n[1] Adicionando papéis RH nos usuários existentes...")
todos = auth_store.listar_admin_rh()
login_para_id = {u["usuario"]: u["id"] for u in todos}

for login, papel_rh in PAPEIS_A_ADICIONAR:
    uid = login_para_id.get(login)
    if not uid:
        print(f"  ⚠  Usuário '{login}' não encontrado — pulando")
        continue
    ok, erro = auth_store.adicionar_papel_rh(uid, papel_rh)
    if ok:
        print(f"  ✓  {login} → {papel_rh}")
    else:
        print(f"  ✗  {login}: {erro}")

# ---------------------------------------------------------------------------
# Passo 2: criar novos usuários
# ---------------------------------------------------------------------------
print("\n[2] Criando novos usuários...")
for dados in NOVOS_USUARIOS:
    novo, erro = auth_store.criar_usuario(
        dados["nome"], dados["usuario"], dados["papel"], dados["email"]
    )
    if erro:
        # Pode já existir — idempotente
        print(f"  ↩  {dados['usuario']}: {erro} (pulando criação)")
        # Mesmo assim tenta adicionar o papel RH se o usuário já existe
        todos_atual = auth_store.listar_admin_rh()
        uid = next((u["id"] for u in todos_atual if u["usuario"] == dados["usuario"]), None)
        if uid:
            ok, e2 = auth_store.adicionar_papel_rh(uid, dados["papel_rh"])
            print(f"  {'✓' if ok else '✗'}  papel_rh {dados['papel_rh']}: {'ok' if ok else e2}")
    else:
        print(f"  ✓  {dados['usuario']} criado (id={novo['id']})")
        ok, e2 = auth_store.adicionar_papel_rh(novo["id"], dados["papel_rh"])
        print(f"  {'✓' if ok else '✗'}  papel_rh {dados['papel_rh']}: {'ok' if ok else e2}")

# ---------------------------------------------------------------------------
# Passo 3: verificação final
# ---------------------------------------------------------------------------
print("\n[3] Estado final da equipe RH:")
print(f"  {'USUÁRIO':<15} {'PAPEL PRINCIPAL':<18} {'PAPÉIS RH'}")
print(f"  {'-'*15} {'-'*18} {'-'*30}")

todos_final = auth_store.listar_admin_rh()
for u in todos_final:
    if not u.get("ativo", True):
        continue
    papeis_rh = [p for p in u.get("papeis_extras", [])
                 if p in auth_store.PAPEIS_RH_VALIDOS]
    eh_coord = u.get("papel") == "coordenador"
    if papeis_rh or eh_coord:
        rh_str = ", ".join(papeis_rh) if papeis_rh else "(herança — coordenador)"
        print(f"  {u['usuario']:<15} {u['papel']:<18} {rh_str}")

print()
print("=" * 60)
print("  Migração concluída.")
print("  Próximo passo: docker restart intranet-safenet")
print("  Depois: GET /api/rh/status (com cookie de sessão) para confirmar")
print("=" * 60)
print()
