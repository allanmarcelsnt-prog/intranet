"""
desempenho_routes.py — Gestor de Avaliação de Desempenho · Safenet Tecnologia
------------------------------------------------------------------------------
MAP-RH-002 · Fatia A: Cadastro de colaboradores + painel de listagem

Registro no main.py — ACIMA do app.mount("/", StaticFiles(...)):
    from desempenho_routes import router as desempenho_router
    app.include_router(desempenho_router)

Prefixo de todas as rotas: /api/rh/desempenho

Padrão de persistência: idêntico ao rh_routes.py
  _ler / _escrever_atomico / _proximo_id / _lock / AGORA
  BASE = Path(__file__).parent · data/ · escrita atômica · lock de thread

Autenticação: exige_papel_rh de auth_routes (campo papeis_extras).
Coordenadores da intranet (papel='coordenador') têm acesso total por herança.

Fatias futuras adicionarão neste mesmo arquivo:
  - Fatia B: critérios técnicos por setor
  - Fatia C: disparo de ciclo + link com token + autoavaliação pública
  - Fatia D: avaliação do avaliador + co-avaliação do supervisor
  - Fatia E: encerramento de ciclo + notificações por e-mail
  - Fatia F: rotina automática de gatilhos
  - Fatia G: painel de acompanhamento completo
"""
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from auth_routes import usuario_logado, exige_papel_rh
import auth_store

router = APIRouter(prefix="/api/rh/desempenho", tags=["desempenho"])

# ---------------------------------------------------------------------------
# PATHS — mesmo padrão BASE = Path(__file__).parent do rh_routes.py
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent
DESEMPENHO_COLABORADORES_PATH = BASE / "data" / "desempenho_colaboradores.json"
DESEMPENHO_CRITERIOS_PATH     = BASE / "data" / "desempenho_criterios.json"

_lock = threading.Lock()

AGORA = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------------------------------------------------------
# CONSTANTES DE DOMÍNIO — alinhadas com MAP-RH-002
# ---------------------------------------------------------------------------
SETORES_VALIDOS = [
    "Operacional", "Monitoramento", "Projetos", "TI",
    "Administrativo", "Licitação",
]

VINCULOS_VALIDOS = ["CLT", "PJ", "Estagiário"]


# ---------------------------------------------------------------------------
# PERSISTÊNCIA — idêntica ao rh_routes.py
# ---------------------------------------------------------------------------
def _ler(path: Path, vazio):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _escrever_atomico(vazio, path)
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Falha ao ler dados: {e}")


def _escrever_atomico(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise HTTPException(status_code=500, detail=f"Falha ao salvar: {e}")


def _proximo_id(lista: list) -> int:
    return max((item.get("id", 0) for item in lista), default=0) + 1


# ---------------------------------------------------------------------------
# MODELOS PYDANTIC
# ---------------------------------------------------------------------------
class NovoColaboradorIn(BaseModel):
    nome: str
    cargo: str
    setor: str                        # vide SETORES_VALIDOS
    email: str
    data_admissao: str                # AAAA-MM-DD
    vinculo: str                      # CLT | PJ | Estagiário
    avaliador_id: int                 # id do usuário avaliador principal
    supervisor_id: Optional[int] = None   # só Operacional técnico/auxiliar


class NovoCriterioIn(BaseModel):
    descricao: str
    setor: str                          # vide SETORES_VALIDOS
    aplicavel_a: list = []             # ex.: ["Técnico", "Instalador"] — vazio = todos do setor


class EditarCriterioIn(BaseModel):
    descricao: Optional[str] = None
    setor: Optional[str] = None
    aplicavel_a: Optional[list] = None
    ativo: Optional[bool] = None


class EditarColaboradorIn(BaseModel):
    nome: Optional[str] = None
    cargo: Optional[str] = None
    setor: Optional[str] = None
    email: Optional[str] = None
    data_admissao: Optional[str] = None
    vinculo: Optional[str] = None
    avaliador_id: Optional[int] = None
    supervisor_id: Optional[int] = None
    ativo: Optional[bool] = None      # false = desligado; preserva histórico


# ---------------------------------------------------------------------------
# DIAGNÓSTICO
# ---------------------------------------------------------------------------
@router.get("/status")
def desempenho_status(user=Depends(usuario_logado)):
    """Diagnóstico do módulo — versão e contagens básicas."""
    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    ativos = [c for c in colaboradores if c.get("ativo", True)]
    por_setor = {}
    for c in ativos:
        s = c.get("setor", "–")
        por_setor[s] = por_setor.get(s, 0) + 1
    return {
        "status": "ok",
        "modulo": "Gestor de Avaliação de Desempenho",
        "versao": "1.0 — Fatia A (cadastro de colaboradores)",
        "colaboradores_total": len(colaboradores),
        "colaboradores_ativos": len(ativos),
        "por_setor": por_setor,
    }


# ---------------------------------------------------------------------------
# AVALIADORES — lista de usuários ativos disponíveis para ser avaliadores
# Usado pelo frontend para popular o <select> no modal de colaborador.
# ---------------------------------------------------------------------------
@router.get("/avaliadores")
def listar_avaliadores(
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Retorna todos os usuários ativos cadastrados na intranet.
    Usado para popular o select de 'Avaliador principal' e 'Supervisor de campo'
    no cadastro de colaboradores.
    """
    todos = auth_store.listar_admin_rh()
    ativos = [
        {
            "id":      u["id"],
            "nome":    u["nome"],
            "usuario": u.get("usuario", ""),
            "papel":   u.get("papel", ""),
        }
        for u in todos
        if u.get("ativo", True)
    ]
    return {"avaliadores": sorted(ativos, key=lambda a: a["nome"].lower())}


# ---------------------------------------------------------------------------
# COLABORADORES
# ---------------------------------------------------------------------------
@router.get("/colaboradores")
def listar_colaboradores(
    setor:  Optional[str]  = None,
    ativo:  Optional[bool] = None,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Lista colaboradores com filtros opcionais de setor e status."""
    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])

    if setor:
        colaboradores = [c for c in colaboradores
                         if (c.get("setor") or "").lower() == setor.lower()]
    if ativo is not None:
        colaboradores = [c for c in colaboradores
                         if c.get("ativo", True) == ativo]

    # enriquecer com nome do avaliador (lookup local para evitar N+1)
    try:
        todos_usuarios = {u["id"]: u["nome"]
                          for u in auth_store.listar_admin_rh()}
    except Exception:
        todos_usuarios = {}

    for c in colaboradores:
        c["_avaliador_nome"]    = todos_usuarios.get(c.get("avaliador_id"), "–")
        c["_supervisor_nome"]   = todos_usuarios.get(c.get("supervisor_id"), None)

    colaboradores = sorted(colaboradores, key=lambda c: c.get("nome", "").lower())
    return {"colaboradores": colaboradores, "total": len(colaboradores)}


@router.get("/colaboradores/{cid}")
def obter_colaborador(
    cid: int,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Retorna um colaborador pelo ID."""
    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    c = next((c for c in colaboradores if c["id"] == cid), None)
    if not c:
        raise HTTPException(status_code=404, detail="Colaborador não encontrado.")
    try:
        todos_usuarios = {u["id"]: u["nome"]
                          for u in auth_store.listar_admin_rh()}
    except Exception:
        todos_usuarios = {}
    c["_avaliador_nome"]  = todos_usuarios.get(c.get("avaliador_id"), "–")
    c["_supervisor_nome"] = todos_usuarios.get(c.get("supervisor_id"), None)
    return {"colaborador": c}


@router.post("/colaboradores")
def criar_colaborador(
    dados: NovoColaboradorIn,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Cadastra um novo colaborador.
    data_admissao é a base para os gatilhos automáticos de ciclo:
      - 35º dia → avaliação de experiência 1
      - 80º dia → avaliação de experiência 2
      - A cada 6 meses a partir da efetivação → avaliação semestral
    """
    nome   = (dados.nome  or "").strip()
    cargo  = (dados.cargo or "").strip()
    email  = (dados.email or "").strip().lower()

    if not nome:
        raise HTTPException(status_code=400, detail="Nome é obrigatório.")
    if not cargo:
        raise HTTPException(status_code=400, detail="Cargo é obrigatório.")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="E-mail válido é obrigatório.")
    if dados.setor not in SETORES_VALIDOS:
        raise HTTPException(status_code=400,
            detail=f"Setor inválido. Válidos: {SETORES_VALIDOS}")
    if dados.vinculo not in VINCULOS_VALIDOS:
        raise HTTPException(status_code=400,
            detail=f"Vínculo inválido. Válidos: {VINCULOS_VALIDOS}")
    try:
        datetime.strptime(dados.data_admissao, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400,
            detail="Data de admissão inválida. Use o formato AAAA-MM-DD.")

    with _lock:
        colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
        if any(c.get("email") == email for c in colaboradores):
            raise HTTPException(status_code=409,
                detail="Já existe um colaborador cadastrado com este e-mail.")
        novo = {
            "id":             _proximo_id(colaboradores),
            "nome":           nome,
            "cargo":          cargo,
            "setor":          dados.setor,
            "email":          email,
            "data_admissao":  dados.data_admissao,
            "vinculo":        dados.vinculo,
            "avaliador_id":   dados.avaliador_id,
            "supervisor_id":  dados.supervisor_id,
            "ativo":          True,
            "cadastrado_por": user["nome"],
            "cadastrado_em":  AGORA(),
        }
        colaboradores.append(novo)
        _escrever_atomico(colaboradores, DESEMPENHO_COLABORADORES_PATH)
    return {"ok": True, "colaborador": novo}


# ---------------------------------------------------------------------------
# CRITÉRIOS TÉCNICOS — MAP-RH-002 Fatia B
# ---------------------------------------------------------------------------
@router.get("/criterios")
def listar_criterios(
    setor: Optional[str] = None,
    ativo: Optional[bool] = None,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Lista critérios técnicos com filtros opcionais de setor e status."""
    criterios = _ler(DESEMPENHO_CRITERIOS_PATH, [])
    if setor:
        criterios = [c for c in criterios
                     if (c.get("setor") or "").lower() == setor.lower()]
    if ativo is not None:
        criterios = [c for c in criterios if c.get("ativo", True) == ativo]
    criterios = sorted(criterios, key=lambda c: (c.get("setor", ""), c.get("id", 0)))
    return {"criterios": criterios, "total": len(criterios)}


@router.post("/criterios")
def criar_criterio(
    dados: NovoCriterioIn,
    user=Depends(exige_papel_rh(["rh_coordenador"]))
):
    """Cadastra um novo critério técnico (restrito ao rh_coordenador)."""
    descricao = (dados.descricao or "").strip()
    if not descricao:
        raise HTTPException(status_code=400, detail="Descrição é obrigatória.")
    if dados.setor not in SETORES_VALIDOS:
        raise HTTPException(status_code=400,
            detail=f"Setor inválido. Válidos: {SETORES_VALIDOS}")
    with _lock:
        criterios = _ler(DESEMPENHO_CRITERIOS_PATH, [])
        # evitar duplicata exata no mesmo setor
        if any(c.get("descricao", "").strip().lower() == descricao.lower()
               and c.get("setor") == dados.setor
               for c in criterios):
            raise HTTPException(status_code=409,
                detail="Já existe um critério com essa descrição neste setor.")
        novo = {
            "id":           _proximo_id(criterios),
            "descricao":    descricao,
            "setor":        dados.setor,
            "aplicavel_a":  dados.aplicavel_a or [],
            "ativo":        True,
            "criado_por":   user["nome"],
            "criado_em":    AGORA(),
        }
        criterios.append(novo)
        _escrever_atomico(criterios, DESEMPENHO_CRITERIOS_PATH)
    return {"ok": True, "criterio": novo}


@router.patch("/criterios/{crit_id}")
def editar_criterio(
    crit_id: int,
    dados: EditarCriterioIn,
    user=Depends(exige_papel_rh(["rh_coordenador"]))
):
    """Edita ou desativa um critério técnico (restrito ao rh_coordenador).
    Usar ativo=false para desativar — critérios inativos não aparecem em novos
    ciclos mas são preservados nos ciclos já iniciados.
    """
    with _lock:
        criterios = _ler(DESEMPENHO_CRITERIOS_PATH, [])
        c = next((c for c in criterios if c["id"] == crit_id), None)
        if not c:
            raise HTTPException(status_code=404, detail="Critério não encontrado.")
        if dados.descricao is not None:
            c["descricao"] = dados.descricao.strip()
        if dados.setor is not None:
            if dados.setor not in SETORES_VALIDOS:
                raise HTTPException(status_code=400,
                    detail=f"Setor inválido. Válidos: {SETORES_VALIDOS}")
            c["setor"] = dados.setor
        if dados.aplicavel_a is not None:
            c["aplicavel_a"] = dados.aplicavel_a
        if dados.ativo is not None:
            c["ativo"] = dados.ativo
        c["editado_por"] = user["nome"]
        c["editado_em"]  = AGORA()
        _escrever_atomico(criterios, DESEMPENHO_CRITERIOS_PATH)
    return {"ok": True, "criterio": c}


@router.patch("/colaboradores/{cid}")
def editar_colaborador(
    cid: int,
    dados: EditarColaboradorIn,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Edita campos do colaborador.
    Usar ativo=false para desligar — preserva histórico de avaliações.
    """
    with _lock:
        colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
        c = next((c for c in colaboradores if c["id"] == cid), None)
        if not c:
            raise HTTPException(status_code=404, detail="Colaborador não encontrado.")

        if dados.nome is not None:
            c["nome"] = dados.nome.strip()
        if dados.cargo is not None:
            c["cargo"] = dados.cargo.strip()
        if dados.setor is not None:
            if dados.setor not in SETORES_VALIDOS:
                raise HTTPException(status_code=400,
                    detail=f"Setor inválido. Válidos: {SETORES_VALIDOS}")
            c["setor"] = dados.setor
        if dados.email is not None:
            email_novo = dados.email.strip().lower()
            if "@" not in email_novo:
                raise HTTPException(status_code=400, detail="E-mail inválido.")
            if any(x.get("email") == email_novo and x["id"] != cid
                   for x in colaboradores):
                raise HTTPException(status_code=409,
                    detail="E-mail já cadastrado em outro colaborador.")
            c["email"] = email_novo
        if dados.data_admissao is not None:
            try:
                datetime.strptime(dados.data_admissao, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=400,
                    detail="Data de admissão inválida. Use AAAA-MM-DD.")
            c["data_admissao"] = dados.data_admissao
        if dados.vinculo is not None:
            if dados.vinculo not in VINCULOS_VALIDOS:
                raise HTTPException(status_code=400,
                    detail=f"Vínculo inválido. Válidos: {VINCULOS_VALIDOS}")
            c["vinculo"] = dados.vinculo
        if dados.avaliador_id is not None:
            c["avaliador_id"] = dados.avaliador_id
        if dados.supervisor_id is not None:
            # permite zerar explicitamente com 0
            c["supervisor_id"] = dados.supervisor_id if dados.supervisor_id != 0 else None
        if dados.ativo is not None:
            c["ativo"] = dados.ativo
            if not dados.ativo and not c.get("desligado_em"):
                c["desligado_em"] = AGORA()

        c["editado_por"] = user["nome"]
        c["editado_em"]  = AGORA()
        _escrever_atomico(colaboradores, DESEMPENHO_COLABORADORES_PATH)
    return {"ok": True, "colaborador": c}
