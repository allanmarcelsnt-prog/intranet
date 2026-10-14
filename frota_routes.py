"""
Rotas do Relatório de Frota para o FastAPI da intranet.

Adicione estas rotas ao seu app FastAPI existente (o mesmo que serve
/api/dados/comercial). Se você usa APIRouter, pode registrar assim:

    from frota_routes import router as frota_router
    app.include_router(frota_router)

Ou copiar os @router... pra dentro do seu arquivo principal trocando
"router" por "app".
"""

import json
import os
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/frota", tags=["frota"])

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO
# O frota.json fica na MESMA pasta data/ do comercial.json.
# Seguimos o mesmo padrão do main.py (BASE = Path(__file__).parent), assim o
# caminho funciona independente do working dir do container — é robusto.
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent
DADOS_PATH = BASE / "data" / "frota.json"

# Estrutura inicial, idêntica à do app (localStorage) — nada muda no formato.
ESTRUTURA_VAZIA = {"veiculos": [], "ativo": None, "dados": {}}

# Cadastros de apoio (motoristas, locais, classificação dos veículos).
# Ficam em arquivo separado: mudam pouco e são lidos por todas as telas.
CADASTROS_PATH = BASE / "data" / "frota_cadastros.json"

# O servidor e a fonte da verdade dos cadastros. Na PRIMEIRA leitura, quando o
# arquivo ainda nao existe, ele nasce ja com os locais do Auvo. Antes, o seed
# vivia so no bundle do front: cada maquina desenhava os 218 locais na tela mas
# o servidor seguia vazio, o que dava a ILUSAO de cadastro global.
try:
    from frota_locais_seed import LOCAIS_SEED
except ImportError:  # se o seed nao subiu junto, degrada sem quebrar
    LOCAIS_SEED = []

# Cargos-semente: defaults que nascem no primeiro boot. A partir daqui a lista
# fica EDITAVEL pela tela (aba Membros da Equipe) e passa a viver em
# cadastros["cargos"]. Adicionar um cargo novo nao exige mais mexer em codigo
# nem recriar o container — vira rota incremental, igual a locais.
CARGOS_SEED = (
    "Técnico", "Auxiliar", "Supervisor", "Coordenador",
    "Técnico Segurança do Trabalho", "Operador de Monitoramento", "Administrativo",
)

# Cadastro unico de pessoas. Cada membro:
#   {"nome": "Sandro Pereira", "cargo": "Técnico", "motorista": True}
# A flag "motorista" e o que alimenta a selecao de Motorista do dia/parada —
# o cargo NAO determina isso (um Coordenador tambem pode dirigir).
CADASTROS_VAZIO = {"membros": [], "locais": list(LOCAIS_SEED), "classificacoes": {}, "cargos": list(CARGOS_SEED)}


def _migrar_cadastros(cad: dict) -> tuple[dict, bool]:
    """
    Converte o formato antigo (motoristas: ["Nome", ...]) para o novo
    (membros: [{nome, cargo, motorista}]).

    Roda na leitura, entao o arquivo que ja esta em producao e convertido na
    primeira vez que alguem abrir o app — ninguem precisa recadastrar.
    Sem cargo conhecido, entra como "" e a tela pede o preenchimento.
    """
    mudou = False
    if "membros" not in cad:
        antigos = cad.pop("motoristas", []) or []
        cad["membros"] = [
            {"nome": n, "cargo": "", "motorista": True}
            for n in antigos
            if isinstance(n, str) and n.strip()
        ]
        mudou = True
    else:
        # normaliza entradas incompletas
        norm = []
        for m in cad.get("membros", []):
            if isinstance(m, str):
                norm.append({"nome": m, "cargo": "", "motorista": True}); mudou = True
            elif isinstance(m, dict) and m.get("nome"):
                novo = {
                    "nome": m["nome"],
                    "cargo": m.get("cargo", "") or "",
                    "motorista": bool(m.get("motorista", False)),
                }
                if novo != m:
                    mudou = True
                norm.append(novo)
            else:
                mudou = True  # descarta lixo
        cad["membros"] = norm
    cad.setdefault("locais", list(LOCAIS_SEED))
    cad.setdefault("classificacoes", {})

    # cargos: cadastro editavel. Nasce com os defaults-semente e faz UNIAO com
    # qualquer cargo ja usado por um membro, pra nunca sumir do dropdown um cargo
    # valido (ex.: cadastros vindos da versao de tupla fixa).
    if "cargos" not in cad or not isinstance(cad["cargos"], list):
        cad["cargos"] = list(CARGOS_SEED)
        mudou = True
    for c in (m.get("cargo", "") for m in cad.get("membros", [])):
        if c and c not in cad["cargos"]:
            cad["cargos"].append(c)
            mudou = True

    cad.pop("motoristas", None)
    return cad, mudou

# Lock em processo: serializa escritas concorrentes dentro deste servidor,
# evitando que dois POST simultâneos corrompam o arquivo (read-modify-write).
_lock = threading.Lock()


def _garantir_arquivo(path: Path = None, vazio: dict = None) -> None:
    """Cria a pasta e um arquivo vazio válido caso ainda não existam."""
    path = path or DADOS_PATH
    vazio = vazio if vazio is not None else ESTRUTURA_VAZIA
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _escrever_atomico(vazio, path)


def _ler(path: Path = None, vazio: dict = None) -> dict:
    path = path or DADOS_PATH
    vazio = vazio if vazio is not None else ESTRUTURA_VAZIA
    _garantir_arquivo(path, vazio)
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Falha ao ler dados: {e}")


def _escrever_atomico(obj: dict, path: Path = None) -> None:
    """
    Escreve em arquivo temporário e faz rename atômico por cima do alvo.
    Isso garante que um leitor nunca veja um arquivo pela metade, mesmo
    se o servidor cair no meio da escrita.
    """
    path = path or DADOS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # rename atômico
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise HTTPException(status_code=500, detail=f"Falha ao salvar: {e}")


# ---------------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------------
class SalvarDiaPayload(BaseModel):
    placa: str          # placa do veículo (chave em "dados")
    data: str           # identificador do dia (ex.: "2026-06-16")
    dia: dict           # o objeto do dia (linha do tempo, não previstos, etc.)
    definir_ativo: bool = False  # opcional: marca esta placa como a ativa


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------
@router.get("/dados")
def obter_dados() -> dict:
    """Devolve o objeto inteiro { veiculos, ativo, dados }. Só leitura."""
    return _ler()


@router.post("/dia")
def salvar_dia(payload: SalvarDiaPayload) -> dict:
    """
    Grava APENAS o dia de um veículo (escrita por veículo/dia).

    Faz read-modify-write sob lock: lê o objeto atual, insere/atualiza
    só a fatia dados[placa][data], e regrava. Assim dois operadores em
    veículos ou dias diferentes não se sobrescrevem — o único caso de
    colisão real é dois salvamentos do MESMO veículo+dia, que é raro.
    """
    with _lock:
        estado = _ler()

        # registra a placa na lista de veículos, se ainda não existir
        if payload.placa not in estado["veiculos"]:
            estado["veiculos"].append(payload.placa)

        # garante o acervo do veículo
        estado["dados"].setdefault(payload.placa, {})

        # insere/atualiza só o dia
        estado["dados"][payload.placa][payload.data] = payload.dia

        if payload.definir_ativo:
            estado["ativo"] = payload.placa

        _escrever_atomico(estado)

    return {"ok": True, "placa": payload.placa, "data": payload.data}


@router.delete("/dia")
def apagar_dia(placa: str, data: str) -> dict:
    """Remove um dia específico de um veículo (opcional, mas útil)."""
    with _lock:
        estado = _ler()
        acervo = estado["dados"].get(placa, {})
        if data in acervo:
            del acervo[data]
            _escrever_atomico(estado)
            return {"ok": True, "removido": True, "placa": placa, "data": data}
        return {"ok": True, "removido": False, "placa": placa, "data": data}


# ---------------------------------------------------------------------------
# CADASTROS DE APOIO (motoristas / locais / classificação de veículos)
#
# Ficam num arquivo separado do frota.json porque têm ciclo de vida próprio:
# mudam raramente, são compartilhados por todos os veículos, e não queremos
# que um POST de cadastro dispute lock com o salvamento de um dia.
# ---------------------------------------------------------------------------
_lock_cad = threading.Lock()


class CadastrosPayload(BaseModel):
    """Substituicao explicita de listas inteiras (usar so em importacao/backup)."""
    membros: list | None = None
    locais: list | None = None
    classificacoes: dict | None = None
    cargos: list | None = None


class MembroPayload(BaseModel):
    nome: str
    cargo: str = ""
    motorista: bool = False
    nome_antigo: str | None = None   # preenchido ao renomear


class LocalPayload(BaseModel):
    nome: str
    endereco: str = ""
    complemento: str = ""
    nome_antigo: str | None = None   # preenchido ao renomear


class ClassificacaoPayload(BaseModel):
    placa: str
    classificacao: str


class CargoPayload(BaseModel):
    nome: str
    nome_antigo: str | None = None   # preenchido ao renomear


@router.get("/cadastros")
def obter_cadastros() -> dict:
    """
    Devolve { membros, locais, classificacoes }.

    Se o arquivo ainda nao existe, ele nasce com o seed de locais — assim toda
    maquina le a MESMA lista, vinda do servidor. Se existir no formato antigo
    (motoristas como lista de nomes), e migrado e regravado aqui mesmo.
    """
    with _lock_cad:
        cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)
        cad, mudou = _migrar_cadastros(cad)
        if mudou:
            _escrever_atomico(cad, CADASTROS_PATH)
        return cad


# --- membros: uma rota por INTENCAO, nao por estado --------------------------
# O cliente diz "adicione o Sandro", nao "a lista agora e [Sandro]". Assim duas
# maquinas que cadastram gente diferente ao mesmo tempo somam, em vez de uma
# apagar a outra.

@router.post("/cadastros/membro")
def upsert_membro(p: MembroPayload) -> dict:
    """
    Cria ou edita um membro da equipe.

    nome_antigo AUSENTE  -> criacao: recusa nome ja existente (409).
    nome_antigo PRESENTE -> edicao (permite renomear e trocar cargo/flag).
    """
    nome = p.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome vazio.")
    novo = {"nome": nome, "cargo": p.cargo, "motorista": bool(p.motorista)}

    with _lock_cad:
        cad, _ = _migrar_cadastros(_ler(CADASTROS_PATH, CADASTROS_VAZIO))
        # cargo (quando informado) precisa existir na lista editavel de cargos;
        # vazio continua permitido = "sem cargo definido".
        if p.cargo and p.cargo not in cad.get("cargos", []):
            raise HTTPException(status_code=400, detail="Cargo invalido. Cadastre-o antes na lista de cargos.")
        membros = cad["membros"]
        # comparacao sem diferenciar maiuscula: "sandro" e "Sandro" sao a mesma pessoa
        acha = lambda n: next(
            (i for i, m in enumerate(membros) if m["nome"].casefold() == n.casefold()), None
        )

        if p.nome_antigo is None:
            if acha(nome) is not None:
                raise HTTPException(status_code=409, detail="Ja existe um membro com esse nome.")
            membros.append(novo)
        else:
            idx = acha(p.nome_antigo.strip())
            if idx is None:
                raise HTTPException(status_code=404, detail="Membro nao encontrado para edicao.")
            colisao = acha(nome)
            if colisao is not None and colisao != idx:
                raise HTTPException(status_code=409, detail="Ja existe um membro com esse nome.")
            membros[idx] = novo

        membros.sort(key=lambda m: m["nome"].casefold())
        _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "membros": cad["membros"]}


@router.delete("/cadastros/membro")
def del_membro(nome: str) -> dict:
    with _lock_cad:
        cad, _ = _migrar_cadastros(_ler(CADASTROS_PATH, CADASTROS_VAZIO))
        antes = len(cad["membros"])
        cad["membros"] = [m for m in cad["membros"] if m["nome"].casefold() != nome.casefold()]
        if len(cad["membros"]) != antes:
            _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "membros": cad["membros"]}


# --- locais -----------------------------------------------------------------
@router.post("/cadastros/local")
def upsert_local(p: LocalPayload) -> dict:
    """
    Cria ou edita um local.

    nome_antigo AUSENTE  -> criacao: recusa se o nome ja existir (409).
    nome_antigo PRESENTE -> edicao do local com aquele nome (permite renomear).

    A distincao importa: sem ela, cadastrar um nome ja existente sobrescreveria
    o endereco do local do colega em silencio, achando que era uma edicao.
    """
    nome = p.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome do local vazio.")
    novo = {"nome": nome, "endereco": p.endereco.strip(), "complemento": p.complemento.strip()}

    with _lock_cad:
        cad, _ = _migrar_cadastros(_ler(CADASTROS_PATH, CADASTROS_VAZIO))
        locais = cad["locais"]
        existe = lambda n: next((i for i, l in enumerate(locais) if l.get("nome") == n), None)

        if p.nome_antigo is None:
            # criacao
            if existe(nome) is not None:
                raise HTTPException(status_code=409, detail="Ja existe um local com esse nome.")
            locais.append(novo)
        else:
            # edicao
            idx = existe(p.nome_antigo.strip())
            if idx is None:
                raise HTTPException(status_code=404, detail="Local nao encontrado para edicao.")
            # renomeando para um nome que ja e de OUTRO local? recusa.
            colisao = existe(nome)
            if colisao is not None and colisao != idx:
                raise HTTPException(status_code=409, detail="Ja existe um local com esse nome.")
            locais[idx] = novo

        locais.sort(key=lambda l: l.get("nome", "").casefold())
        _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "total": len(cad["locais"])}


@router.delete("/cadastros/local")
def del_local(nome: str) -> dict:
    with _lock_cad:
        cad, _ = _migrar_cadastros(_ler(CADASTROS_PATH, CADASTROS_VAZIO))
        antes = len(cad["locais"])
        cad["locais"] = [l for l in cad["locais"] if l.get("nome") != nome]
        if len(cad["locais"]) != antes:
            _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "total": len(cad["locais"])}


# --- classificacao de veiculo ----------------------------------------------
@router.post("/cadastros/classificacao")
def set_classificacao(p: ClassificacaoPayload) -> dict:
    if p.classificacao not in ("Light", "Operacional"):
        raise HTTPException(status_code=400, detail="Classificacao invalida.")
    with _lock_cad:
        cad, _ = _migrar_cadastros(_ler(CADASTROS_PATH, CADASTROS_VAZIO))
        cad["classificacoes"][p.placa] = p.classificacao
        _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "classificacoes": cad["classificacoes"]}


@router.delete("/cadastros/classificacao")
def del_classificacao(placa: str) -> dict:
    with _lock_cad:
        cad, _ = _migrar_cadastros(_ler(CADASTROS_PATH, CADASTROS_VAZIO))
        if placa in cad["classificacoes"]:
            del cad["classificacoes"][placa]
            _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True}


# --- substituicao em massa (backup/importacao) ------------------------------
@router.post("/cadastros")
def salvar_cadastros(payload: CadastrosPayload) -> dict:
    """
    Substitui listas inteiras. Destrutivo por natureza — use as rotas de item
    para o dia a dia. Existe para restaurar backup e para o front antigo nao
    quebrar durante a transicao.
    """
    with _lock_cad:
        cad, _ = _migrar_cadastros(_ler(CADASTROS_PATH, CADASTROS_VAZIO))
        if payload.membros is not None:
            cad["membros"] = payload.membros
        if payload.locais is not None:
            cad["locais"] = payload.locais
        if payload.classificacoes is not None:
            merged = dict(cad.get("classificacoes", {}))
            merged.update(payload.classificacoes)
            cad["classificacoes"] = merged
        if payload.cargos is not None:
            cad["cargos"] = payload.cargos
        _escrever_atomico(cad, CADASTROS_PATH)
    return {
        "ok": True,
        "membros": len(cad.get("membros", [])),
        "locais": len(cad.get("locais", [])),
    }


@router.get("/cadastros/cargos")
def listar_cargos() -> dict:
    """Cargos aceitos (lista editavel). O front monta o select a partir daqui."""
    cad, mudou = _migrar_cadastros(_ler(CADASTROS_PATH, CADASTROS_VAZIO))
    if mudou:
        with _lock_cad:
            _escrever_atomico(cad, CADASTROS_PATH)
    return {"cargos": cad["cargos"]}


@router.post("/cadastros/cargo")
def upsert_cargo(p: CargoPayload) -> dict:
    """
    Cria ou renomeia um cargo.

    nome_antigo AUSENTE  -> criacao: recusa se ja existir (409).
    nome_antigo PRESENTE -> renomeia; propaga o novo nome para os membros que
                            usavam o antigo, pra nao deixar ninguem orfao.
    """
    nome = p.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome do cargo vazio.")

    with _lock_cad:
        cad, _ = _migrar_cadastros(_ler(CADASTROS_PATH, CADASTROS_VAZIO))
        cargos = cad["cargos"]
        acha = lambda n: next((i for i, c in enumerate(cargos) if c.casefold() == n.casefold()), None)

        if p.nome_antigo is None:
            if acha(nome) is not None:
                raise HTTPException(status_code=409, detail="Ja existe um cargo com esse nome.")
            cargos.append(nome)
        else:
            idx = acha(p.nome_antigo.strip())
            if idx is None:
                raise HTTPException(status_code=404, detail="Cargo nao encontrado para edicao.")
            colisao = acha(nome)
            if colisao is not None and colisao != idx:
                raise HTTPException(status_code=409, detail="Ja existe um cargo com esse nome.")
            antigo = cargos[idx]
            cargos[idx] = nome
            for m in cad["membros"]:
                if m.get("cargo") == antigo:
                    m["cargo"] = nome  # propaga a renomeacao

        cargos.sort(key=str.casefold)
        _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "cargos": cad["cargos"]}


@router.delete("/cadastros/cargo")
def del_cargo(nome: str) -> dict:
    """
    Remove um cargo. Bloqueia (409) se algum membro ainda o usa — assim ninguem
    fica com um cargo que sumiu do dropdown.
    """
    with _lock_cad:
        cad, _ = _migrar_cadastros(_ler(CADASTROS_PATH, CADASTROS_VAZIO))
        em_uso = [m["nome"] for m in cad["membros"] if m.get("cargo") == nome]
        if em_uso:
            raise HTTPException(
                status_code=409,
                detail=f"{len(em_uso)} membro(s) usam esse cargo. Troque o cargo deles antes de remover.",
            )
        antes = len(cad["cargos"])
        cad["cargos"] = [c for c in cad["cargos"] if c != nome]
        if len(cad["cargos"]) != antes:
            _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "cargos": cad["cargos"]}
