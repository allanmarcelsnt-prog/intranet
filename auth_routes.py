"""
auth_routes.py — autenticação CENTRAL da intranet Safenet.

Registro no main.py — ACIMA do app.mount("/", StaticFiles(...)):

    from auth_routes import router as auth_router
    app.include_router(auth_router)

Se registrado DEPOIS do mount catch-all, o StaticFiles engole /api/auth/...
e as rotas não respondem. (Mesma regra do frota e do monitoramento.)

Oferece a dependência `usuario_logado` que qualquer app usa para exigir
login e saber, de forma confiável, quem está agindo — base para auditoria.
"""
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from pydantic import BaseModel

import auth_store

router = APIRouter(prefix="/api/auth", tags=["auth"])

SEGREDO = os.environ.get("INTRANET_JWT_SECRET", "troque-este-segredo-em-producao")
DIAS = 7
COOKIE = "intranet_sessao"


def _gerar_token(user: dict) -> str:
    payload = {**user, "exp": datetime.now(timezone.utc) + timedelta(days=DIAS)}
    return jwt.encode(payload, SEGREDO, algorithm="HS256")


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                    max_age=DIAS * 24 * 3600)


def usuario_logado(request: Request) -> dict:
    """Dependência: exige sessão válida. Devolve o usuário autenticado.
    Inclui 'papeis_extras' (lista de papéis RH) — retrocompatível com tokens
    antigos que não trazem esse campo (fica lista vazia)."""
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Não autenticado")
    try:
        dados = jwt.decode(token, SEGREDO, algorithms=["HS256"])
        return {"id": dados["id"], "nome": dados["nome"],
                "usuario": dados["usuario"], "papel": dados["papel"],
                "papeis_extras": dados.get("papeis_extras", [])}
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada")


# Login do administrador do sistema (gerenciamento de usuários). Restrito.
ADMIN_LOGIN = os.environ.get("INTRANET_ADMIN_LOGIN", "allan")

def exige_allan(user=Depends(usuario_logado)) -> dict:
    """Só o administrador (Allan) acessa o gerenciamento de usuários."""
    if user.get("usuario") != ADMIN_LOGIN:
        raise HTTPException(status_code=403, detail="Apenas o administrador pode gerenciar usuários.")
    return user


def exige_coordenador(user=Depends(usuario_logado)) -> dict:
    """Mais permissivo que exige_allan: qualquer coordenador (Allan e Bruno),
    não só o administrador único. Usado só onde a ação não é 'administração
    completa' (criar/desativar/mudar papel continua exige_allan) — hoje serve
    pra editar e-mail de contato, usado pelo sistema de notificação."""
    if user.get("papel") != "coordenador":
        raise HTTPException(status_code=403, detail="Apenas coordenador pode fazer isso.")
    return user


class LoginIn(BaseModel):
    usuario: str
    senha: str

class DefinirSenhaIn(BaseModel):
    usuario: str
    senha: str

class TrocarSenhaIn(BaseModel):
    senha_atual: str
    senha_nova: str


@router.get("/usuarios")
def usuarios():
    """Lista pública (nome, login, tem_senha) para a tela de login."""
    return auth_store.listar_publico()


@router.post("/definir-senha")
def definir_senha(dados: DefinirSenhaIn, resp: Response):
    """Primeiro acesso: cria a própria senha e já entra."""
    ok, erro = auth_store.definir_senha_primeiro_acesso(dados.usuario, dados.senha)
    if not ok:
        raise HTTPException(status_code=400, detail=erro)
    user = auth_store.verificar_senha(dados.usuario, dados.senha)
    _set_cookie(resp, _gerar_token(user))
    return {"user": user}


@router.post("/login")
def login(dados: LoginIn, resp: Response):
    user = auth_store.verificar_senha(dados.usuario, dados.senha)
    if not user:
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos")
    _set_cookie(resp, _gerar_token(user))
    return {"user": user}


@router.post("/logout")
def logout(resp: Response):
    resp.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/eu")
def eu(user=Depends(usuario_logado)):
    return {"user": user}


@router.post("/trocar-senha")
def trocar_senha(dados: TrocarSenhaIn, user=Depends(usuario_logado)):
    ok, erro = auth_store.trocar_senha(user["usuario"], dados.senha_atual, dados.senha_nova)
    if not ok:
        raise HTTPException(status_code=400, detail=erro)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Gerenciamento de usuários — restrito ao administrador (Allan).
# Cria usuário, reseta senha (zera -> pessoa redefine no acesso), muda papel,
# ativa/desativa. Travas de autoproteção: o admin não pode se rebaixar, se
# desativar, nem resetar a própria senha por aqui.
# ---------------------------------------------------------------------------
class CriarUsuarioIn(BaseModel):
    nome: str
    usuario: str
    papel: str
    email: str

class MudarPapelIn(BaseModel):
    papel: str

class AtivoIn(BaseModel):
    ativo: bool

class EmailIn(BaseModel):
    email: str


@router.get("/admin/usuarios")
def admin_listar(user=Depends(exige_allan)):
    """Lista completa (inclui inativos) para a tela de gerenciamento."""
    return {"usuarios": auth_store.listar_admin(), "papeis": auth_store.PAPEIS_VALIDOS}


@router.post("/admin/usuarios")
def admin_criar(dados: CriarUsuarioIn, user=Depends(exige_allan)):
    novo, erro = auth_store.criar_usuario(dados.nome, dados.usuario, dados.papel, dados.email)
    if erro:
        raise HTTPException(status_code=400, detail=erro)
    return {"ok": True, "usuario": novo}


@router.get("/admin/usuarios/emails")
def admin_listar_emails(user=Depends(exige_coordenador)):
    """Nome + papel + e-mail dos usuários ativos — pro painel de e-mail
    (qualquer coordenador, não só o administrador)."""
    return {"usuarios": auth_store.listar_emails()}


@router.post("/admin/usuarios/{user_id}/email")
def admin_definir_email(user_id: int, dados: EmailIn, user=Depends(exige_coordenador)):
    """Define/corrige o e-mail de contato de um usuário. Qualquer coordenador
    pode chamar (diferente das outras ações de admin, restritas ao Allan)."""
    ok, erro = auth_store.definir_email(user_id, dados.email)
    if not ok:
        raise HTTPException(status_code=400, detail=erro)
    return {"ok": True}


@router.post("/admin/usuarios/{user_id}/resetar-senha")
def admin_resetar_senha(user_id: int, user=Depends(exige_allan)):
    if user_id == user["id"]:
        raise HTTPException(status_code=400,
            detail="Para trocar a sua própria senha, use 'trocar senha'.")
    ok, erro = auth_store.resetar_senha(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail=erro)
    return {"ok": True}


@router.post("/admin/usuarios/{user_id}/papel")
def admin_mudar_papel(user_id: int, dados: MudarPapelIn, user=Depends(exige_allan)):
    if user_id == user["id"]:
        raise HTTPException(status_code=400,
            detail="Você não pode alterar o seu próprio papel.")
    ok, erro = auth_store.mudar_papel(user_id, dados.papel)
    if not ok:
        raise HTTPException(status_code=400, detail=erro)
    return {"ok": True}


@router.post("/admin/usuarios/{user_id}/ativo")
def admin_set_ativo(user_id: int, dados: AtivoIn, user=Depends(exige_allan)):
    if user_id == user["id"] and not dados.ativo:
        raise HTTPException(status_code=400,
            detail="Você não pode desativar o seu próprio acesso.")
    ok, erro = auth_store.set_ativo(user_id, dados.ativo)
    if not ok:
        raise HTTPException(status_code=404, detail=erro)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Papéis RH — gerenciamento aditivo.
# Não toca no campo 'papel' principal (Reggra/Frota/Monitoramento intocados).
# Restrito ao administrador (Allan) — mesma regra das outras ações de admin.
# ---------------------------------------------------------------------------

def exige_papel_rh(papeis_permitidos: list):
    """Factory de dependência para o Gestor de RH.
    Uso: user = Depends(exige_papel_rh(['rh_operador', 'rh_coordenador']))
    Coordenadores da intranet têm acesso total ao RH por herança."""
    def _check(user=Depends(usuario_logado)) -> dict:
        if not auth_store.tem_qualquer_papel_rh(user, papeis_permitidos):
            raise HTTPException(status_code=403,
                detail=f"Acesso negado. Papéis necessários: {papeis_permitidos}")
        return user
    return _check


class PapelRHIn(BaseModel):
    papel_rh: str


@router.get("/admin/usuarios/rh")
def admin_listar_rh(user=Depends(exige_allan)):
    """Lista completa com papéis RH incluídos — para o painel de gestão do RH."""
    return {
        "usuarios": auth_store.listar_admin_rh(),
        "papeis_rh": auth_store.PAPEIS_RH_VALIDOS
    }


@router.post("/admin/usuarios/{user_id}/papel-rh")
def admin_adicionar_papel_rh(user_id: int, dados: PapelRHIn, user=Depends(exige_allan)):
    """Adiciona um papel RH ao usuário sem alterar o papel principal.
    Idempotente — pode chamar mais de uma vez sem duplicar."""
    ok, erro = auth_store.adicionar_papel_rh(user_id, dados.papel_rh)
    if not ok:
        raise HTTPException(status_code=400, detail=erro)
    return {"ok": True}


@router.delete("/admin/usuarios/{user_id}/papel-rh/{papel_rh}")
def admin_remover_papel_rh(user_id: int, papel_rh: str, user=Depends(exige_allan)):
    """Remove um papel RH do usuário."""
    ok, erro = auth_store.remover_papel_rh(user_id, papel_rh)
    if not ok:
        raise HTTPException(status_code=400, detail=erro)
    return {"ok": True}
