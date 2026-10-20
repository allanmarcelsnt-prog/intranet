"""
auth_store.py — armazenamento de identidade CENTRAL da intranet Safenet.
Compartilhado por todos os apps (Licitações agora; Frota/Monitoramento depois).

Segue o mesmo padrão de persistência do frota_routes.py:
  BASE = Path(__file__).parent  ·  data/  ·  escrita atômica (tmp + rename)  ·  lock

Dados: data/intranet_usuarios.json
  cada usuário: {id, nome, usuario, papel, senha_hash|None, ativo}
Senhas com bcrypt. Primeiro acesso: senha_hash é None -> a pessoa cria a senha.
"""
import json
import os
import re
import tempfile
import threading
from pathlib import Path

import bcrypt

BASE = Path(__file__).parent
USUARIOS_PATH = BASE / "data" / "intranet_usuarios.json"

_lock = threading.Lock()

_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _email_valido(email: str) -> bool:
    return bool(email) and bool(_EMAIL_REGEX.match(email.strip()))


def _garantir_arquivo() -> None:
    USUARIOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not USUARIOS_PATH.exists():
        _escrever_atomico([])


def _ler() -> list:
    _garantir_arquivo()
    try:
        with USUARIOS_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _escrever_atomico(obj) -> None:
    USUARIOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(USUARIOS_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, USUARIOS_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def listar_publico() -> list:
    """Lista sem dados sensíveis — para a tela de login/seleção."""
    return [
        {"id": u["id"], "nome": u["nome"], "usuario": u["usuario"],
         "papel": u["papel"], "papeis_extras": u.get("papeis_extras", []),
         "tem_senha": bool(u.get("senha_hash"))}
        for u in _ler() if u.get("ativo", True)
    ]


def achar_por_login(usuario: str):
    for u in _ler():
        if u["usuario"] == usuario and u.get("ativo", True):
            return u
    return None


def achar_por_email(email: str):
    """Usuário ativo pelo e-mail (login único Keycloak). None se não houver."""
    email = (email or "").strip().lower()
    if not email:
        return None
    for u in _ler():
        if (u.get("email") or "").strip().lower() == email and u.get("ativo", True):
            return u
    return None


def definir_senha_primeiro_acesso(usuario: str, senha: str):
    with _lock:
        usuarios = _ler()
        for u in usuarios:
            if u["usuario"] == usuario and u.get("ativo", True):
                if u.get("senha_hash"):
                    return False, "Usuário já tem senha definida"
                if len(senha) < 6:
                    return False, "A senha deve ter ao menos 6 caracteres"
                u["senha_hash"] = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
                _escrever_atomico(usuarios)
                return True, None
    return False, "Usuário não encontrado"


def verificar_senha(usuario: str, senha: str):
    u = achar_por_login(usuario)
    if not u or not u.get("senha_hash"):
        return None
    if bcrypt.checkpw(senha.encode(), u["senha_hash"].encode()):
        return {"id": u["id"], "nome": u["nome"], "usuario": u["usuario"],
                "papel": u["papel"], "papeis_extras": u.get("papeis_extras", [])}
    return None


def trocar_senha(usuario: str, senha_atual: str, senha_nova: str):
    with _lock:
        usuarios = _ler()
        for u in usuarios:
            if u["usuario"] == usuario and u.get("ativo", True):
                if not u.get("senha_hash") or not bcrypt.checkpw(senha_atual.encode(), u["senha_hash"].encode()):
                    return False, "Senha atual incorreta"
                if len(senha_nova) < 6:
                    return False, "A nova senha deve ter ao menos 6 caracteres"
                u["senha_hash"] = bcrypt.hashpw(senha_nova.encode(), bcrypt.gensalt()).decode()
                _escrever_atomico(usuarios)
                return True, None
    return False, "Usuário não encontrado"


def semear_equipe(equipe: list) -> int:
    """Cria os usuários da equipe SEM senha (definem no 1º acesso). Idempotente.
    Cada item de 'equipe' pode trazer 'email' opcionalmente (retrocompatível
    com chamadas antigas que não passam esse campo — fica None nesse caso)."""
    with _lock:
        usuarios = _ler()
        existentes = {u["usuario"] for u in usuarios}
        prox_id = max((u["id"] for u in usuarios), default=0) + 1
        for p in equipe:
            if p["usuario"] not in existentes:
                usuarios.append({
                    "id": prox_id, "nome": p["nome"], "usuario": p["usuario"],
                    "papel": p["papel"], "email": p.get("email"), "senha_hash": None, "ativo": True,
                })
                prox_id += 1
        _escrever_atomico(usuarios)
        return len(usuarios)


# ---------------------------------------------------------------------------
# Administração de usuários (tela de gerenciamento — restrita ao Allan).
# ---------------------------------------------------------------------------
# Papéis do Reggra (Licitações) — inalterados
PAPEIS_VALIDOS = ["analista", "analista_cotacao", "comercial", "atribuidor", "operacional", "coordenador"]

# Papéis do Gestor de RH — adicionados de forma aditiva.
# Ficam em 'papeis_extras' no usuário, sem tocar no campo 'papel' principal.
# Isso garante zero impacto no Reggra, Frota e Monitoramento.
PAPEIS_RH_VALIDOS = ["rh_operador", "rh_coordenador", "rh_tecnico", "rh_solicitante"]

# Lista completa (Reggra + RH) — usada apenas no admin para validação geral
PAPEIS_TODOS = PAPEIS_VALIDOS + PAPEIS_RH_VALIDOS


def listar_admin() -> list:
    """Lista completa para a tela de gerenciamento: inclui inativos e o campo
    'ativo'. NUNCA devolve o hash da senha — só se a pessoa já tem senha.
    'email' pode vir None em usuários criados antes do campo existir."""
    return [
        {"id": u["id"], "nome": u["nome"], "usuario": u["usuario"],
         "papel": u["papel"], "ativo": u.get("ativo", True), "email": u.get("email"),
         "tem_senha": bool(u.get("senha_hash"))}
        for u in _ler()
    ]


def listar_emails() -> list:
    """Nome + papel + papeis_extras + e-mail dos usuários ATIVOS."""
    return [
        {"id": u["id"], "nome": u["nome"], "usuario": u["usuario"],
         "papel": u["papel"], "email": u.get("email"),
         "papeis_extras": u.get("papeis_extras", [])}
        for u in _ler() if u.get("ativo", True)
    ]


def definir_email(user_id: int, email: str):
    """Define/corrige o e-mail de um usuário já existente. Usado tanto pelo
    administrador (tela de gerenciamento) quanto por qualquer coordenador
    (painel de e-mail) — a checagem de quem pode chamar isso é feita na
    rota, aqui só valida o dado em si."""
    email = (email or "").strip().lower()
    if not email:
        return False, "E-mail é obrigatório"
    if not _email_valido(email):
        return False, "E-mail inválido"
    with _lock:
        usuarios = _ler()
        if any(u["id"] != user_id and (u.get("email") or "").lower() == email for u in usuarios):
            return False, "Já existe um usuário com esse e-mail"
        for u in usuarios:
            if u["id"] == user_id:
                u["email"] = email
                _escrever_atomico(usuarios)
                return True, None
    return False, "Usuário não encontrado"


def criar_usuario(nome: str, usuario: str, papel: str, email: str):
    """Cria um usuário SEM senha (define no 1º acesso). Login único e minúsculo.
    E-mail obrigatório, validado e único (usado pelo sistema de notificação)."""
    nome = (nome or "").strip()
    usuario = (usuario or "").strip().lower()
    email = (email or "").strip().lower()
    if not nome or not usuario:
        return None, "Nome e login são obrigatórios"
    if not email:
        return None, "E-mail é obrigatório"
    if not _email_valido(email):
        return None, "E-mail inválido"
    if papel not in PAPEIS_VALIDOS:
        return None, "Papel inválido"
    if not usuario.replace("_", "").replace(".", "").isalnum():
        return None, "Login deve conter apenas letras, números, ponto ou underline"
    with _lock:
        usuarios = _ler()
        if any(u["usuario"] == usuario for u in usuarios):
            return None, "Já existe um usuário com esse login"
        if any((u.get("email") or "").lower() == email for u in usuarios):
            return None, "Já existe um usuário com esse e-mail"
        prox_id = max((u["id"] for u in usuarios), default=0) + 1
        novo = {"id": prox_id, "nome": nome, "usuario": usuario,
                "papel": papel, "email": email, "senha_hash": None, "ativo": True}
        usuarios.append(novo)
        _escrever_atomico(usuarios)
        return {"id": novo["id"], "nome": novo["nome"], "usuario": novo["usuario"],
                "papel": novo["papel"], "email": novo["email"], "ativo": True, "tem_senha": False}, None


def resetar_senha(user_id: int):
    """Zera a senha: a pessoa define uma nova no próximo acesso (fluxo de 1º acesso)."""
    with _lock:
        usuarios = _ler()
        for u in usuarios:
            if u["id"] == user_id:
                u["senha_hash"] = None
                _escrever_atomico(usuarios)
                return True, None
    return False, "Usuário não encontrado"


def mudar_papel(user_id: int, papel: str):
    if papel not in PAPEIS_VALIDOS:
        return False, "Papel inválido"
    with _lock:
        usuarios = _ler()
        for u in usuarios:
            if u["id"] == user_id:
                u["papel"] = papel
                _escrever_atomico(usuarios)
                return True, None
    return False, "Usuário não encontrado"


def set_ativo(user_id: int, ativo: bool):
    """Ativa/desativa o acesso (preserva o histórico — não apaga o usuário)."""
    with _lock:
        usuarios = _ler()
        for u in usuarios:
            if u["id"] == user_id:
                u["ativo"] = bool(ativo)
                _escrever_atomico(usuarios)
                return True, None
    return False, "Usuário não encontrado"


# ---------------------------------------------------------------------------
# Papéis extras de RH — campo 'papeis_extras' (lista) no usuário.
# Zero impacto no campo 'papel' principal (Reggra/Frota/Monitoramento).
# Um usuário pode ser 'operacional' no Reggra e 'rh_tecnico' no RH.
# ---------------------------------------------------------------------------

def tem_papel_rh(user: dict, papel_rh: str) -> bool:
    """Verifica se o usuário tem um papel RH específico.
    Coordenadores da intranet têm acesso total ao RH por herança."""
    if user.get("papel") == "coordenador":
        return True
    return papel_rh in user.get("papeis_extras", [])


def tem_qualquer_papel_rh(user: dict, papeis: list) -> bool:
    """Verifica se o usuário tem ao menos um dos papéis RH listados."""
    if user.get("papel") == "coordenador":
        return True
    extras = set(user.get("papeis_extras", []))
    return bool(extras.intersection(set(papeis)))


def adicionar_papel_rh(user_id: int, papel_rh: str):
    """Adiciona um papel RH ao usuário sem alterar o papel principal.
    Idempotente — não duplica se já tiver."""
    if papel_rh not in PAPEIS_RH_VALIDOS:
        return False, f"Papel RH inválido: {papel_rh}"
    with _lock:
        usuarios = _ler()
        for u in usuarios:
            if u["id"] == user_id:
                extras = u.get("papeis_extras", [])
                if papel_rh not in extras:
                    extras.append(papel_rh)
                    u["papeis_extras"] = extras
                    _escrever_atomico(usuarios)
                return True, None
    return False, "Usuário não encontrado"


def remover_papel_rh(user_id: int, papel_rh: str):
    """Remove um papel RH do usuário."""
    with _lock:
        usuarios = _ler()
        for u in usuarios:
            if u["id"] == user_id:
                extras = u.get("papeis_extras", [])
                if papel_rh in extras:
                    extras.remove(papel_rh)
                    u["papeis_extras"] = extras
                    _escrever_atomico(usuarios)
                return True, None
    return False, "Usuário não encontrado"


def listar_admin_rh() -> list:
    """Lista de usuários com papéis RH incluídos — para o painel de gestão do RH."""
    return [
        {"id": u["id"], "nome": u["nome"], "usuario": u["usuario"],
         "papel": u["papel"], "papeis_extras": u.get("papeis_extras", []),
         "ativo": u.get("ativo", True), "email": u.get("email"),
         "tem_senha": bool(u.get("senha_hash"))}
        for u in _ler()
    ]
