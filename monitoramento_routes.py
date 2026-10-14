"""
Rotas do Relatório Semanal de Monitoramento para o FastAPI da intranet.

Registro no main.py — ACIMA do app.mount("/", StaticFiles(...)):

    from monitoramento_routes import router as monitoramento_router
    app.include_router(monitoramento_router)

Se o router for registrado DEPOIS do mount catch-all, o StaticFiles engole
as chamadas de /api/monitoramento/... e as rotas não respondem.

--------------------------------------------------------------------------
DIFERENÇA ESTRUTURAL EM RELAÇÃO AO FROTA
--------------------------------------------------------------------------
O frota tem dois eixos: veículo -> dia. Tudo que acontece pertence ao dia.

O monitoramento tem TRÊS eixos, porque pontos de atenção e anormalidades
técnicas atravessam semanas: um ponto identificado na semana 1 e resolvido
na semana 3 precisa aparecer nos relatórios das semanas 1, 2 e 3.

Se fossem gravados dentro do objeto da semana (como no frota), o ponto
aberto na semana 1 sumiria do relatório da semana 2. Por isso:

    semanas[cliente][segunda]  -> eventos que ACONTECEM na semana
                                  (armes, desarmes, disparos, zonas, visitas)
    acervo[cliente]            -> itens que PERSISTEM entre semanas
                                  (pontos de atenção, anormalidades técnicas)

O relatório da semana consulta o acervo filtrando por sobreposição de
período (ver _itens_do_periodo mais abaixo), não por igualdade de chave.
"""

import json
import os
import tempfile
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/monitoramento", tags=["monitoramento"])

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO
# Mesmo padrão do main.py e do frota_routes.py: Path(__file__).parent.
# Nunca caminho relativo solto — depende do working dir do container.
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent
DADOS_PATH = BASE / "data" / "monitoramento.json"
CADASTROS_PATH = BASE / "data" / "monitoramento_cadastros.json"

ESTRUTURA_VAZIA = {
    "versao": 1,
    "clientes": [],      # ["COD001", "COD002"] — códigos, não nomes
    "ativo": None,
    "semanas": {},       # { "COD001": { "2026-07-13": {...semana...} } }
    "acervo": {},        # { "COD001": { "pontos": [...], "anormalidades": [...] } }
}

CADASTROS_VAZIO = {
    "versao": 1,
    "clientes": [],      # [{codigo, nome, endereco, cidade, estado, cep, responsavel}]
    "usuarios": {},      # { "COD001": [{nome, cargo}] }
    "sensores": {},      # { "COD001": [{nome, tipo, localizacao, zona, status, obs}] }
    "dispositivos": [],  # ["Controle Remoto", "Aplicativo My Security", ...]
}

DISPOSITIVOS_PADRAO = [
    "Controle Remoto",
    "Aplicativo My Security",
    "Safenet Monitore",
    "Teclado",
]

_lock = threading.Lock()
_lock_cad = threading.Lock()


# ---------------------------------------------------------------------------
# Persistência (idêntica ao frota: escrita atômica + lock de thread)
# ---------------------------------------------------------------------------
def _escrever_atomico(obj: dict, path: Path) -> None:
    """Grava em temporário e faz rename atômico: leitor nunca vê meio arquivo."""
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


def _garantir_arquivo(path: Path, vazio: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _escrever_atomico(vazio, path)


def _ler(path: Path, vazio: dict) -> dict:
    _garantir_arquivo(path, vazio)
    try:
        with path.open("r", encoding="utf-8") as f:
            return _migrar(json.load(f), vazio)
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Falha ao ler dados: {e}")


def _migrar(estado: dict, vazio: dict) -> dict:
    """
    Normaliza dados legados na carga. Toda mudança de formato futura entra
    aqui — nunca quebrar dados já gravados (regra herdada do frota).
    """
    for chave, valor in vazio.items():
        estado.setdefault(chave, valor if not isinstance(valor, (dict, list)) else type(valor)())
    return estado


# ---------------------------------------------------------------------------
# Semana: chave é sempre a SEGUNDA-FEIRA em ISO (aaaa-mm-dd)
# ---------------------------------------------------------------------------
def _segunda_da_semana(iso: str) -> str:
    """Devolve a segunda-feira da semana que contém a data informada."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Data inválida: {iso}")
    return (d - timedelta(days=d.weekday())).isoformat()


def _domingo_da_semana(segunda_iso: str) -> str:
    return (date.fromisoformat(segunda_iso) + timedelta(days=6)).isoformat()


def _validar_segunda(iso: str) -> str:
    """A chave da semana DEVE ser uma segunda-feira. Normaliza se não for."""
    return _segunda_da_semana(iso)


# ---------------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------------
class SalvarSemanaPayload(BaseModel):
    cliente: str                      # código do cliente
    semana: str                       # ISO da segunda-feira
    dados: dict                       # { armes, desarmes, disparos, zonas, visitas, observacoes }
    definir_ativo: bool = False


class ItemAcervo(BaseModel):
    """
    Base comum a pontos de atenção e anormalidades técnicas.

    data_resolucao = None significa EM ABERTO. É esse campo (e não o status)
    que define a janela de vida do item no filtro por período — o status é
    rótulo de exibição, a data é o fato.
    """
    id: Optional[str] = None
    descricao: str
    data_identificacao: str                        # ISO
    data_resolucao: Optional[str] = None           # ISO ou None
    status: Literal["Em aberto", "Em acompanhamento", "Resolvido"] = "Em aberto"


class PontoAtencao(ItemAcervo):
    prioridade: Literal["Baixa", "Média", "Alta", "Crítica"] = "Média"


class AnormalidadeTecnica(ItemAcervo):
    responsavel: str = ""
    solucao: str = ""


class SalvarItemAcervoPayload(BaseModel):
    cliente: str
    tipo: Literal["pontos", "anormalidades"]
    item: dict


class CadastrosPayload(BaseModel):
    clientes: list = Field(default_factory=list)
    usuarios: dict = Field(default_factory=dict)
    sensores: dict = Field(default_factory=dict)
    dispositivos: list = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ROTAS — dados operacionais
# ---------------------------------------------------------------------------
@router.get("/dados")
def obter_dados() -> dict:
    """Devolve { versao, clientes, ativo, semanas, acervo }. Só leitura."""
    return _ler(DADOS_PATH, ESTRUTURA_VAZIA)


@router.post("/semana")
def salvar_semana(payload: SalvarSemanaPayload) -> dict:
    """
    Grava APENAS uma semana de um cliente (granularidade fina).

    Read-modify-write sob lock: lê o estado, substitui só a fatia
    semanas[cliente][semana], regrava. Dois operadores em clientes ou
    semanas diferentes não se sobrescrevem.
    """
    semana = _validar_segunda(payload.semana)
    with _lock:
        estado = _ler(DADOS_PATH, ESTRUTURA_VAZIA)

        if payload.cliente not in estado["clientes"]:
            estado["clientes"].append(payload.cliente)

        estado["semanas"].setdefault(payload.cliente, {})
        estado["semanas"][payload.cliente][semana] = payload.dados

        if payload.definir_ativo:
            estado["ativo"] = payload.cliente

        _escrever_atomico(estado, DADOS_PATH)

    return {"ok": True, "cliente": payload.cliente, "semana": semana}


@router.delete("/semana")
def apagar_semana(cliente: str, semana: str) -> dict:
    semana = _validar_segunda(semana)
    with _lock:
        estado = _ler(DADOS_PATH, ESTRUTURA_VAZIA)
        acervo_cli = estado["semanas"].get(cliente, {})
        if semana in acervo_cli:
            del acervo_cli[semana]
            _escrever_atomico(estado, DADOS_PATH)
            return {"ok": True, "removido": True, "cliente": cliente, "semana": semana}
        return {"ok": True, "removido": False, "cliente": cliente, "semana": semana}


# ---------------------------------------------------------------------------
# ROTAS — acervo (pontos de atenção / anormalidades)
#
# Vivem fora da semana porque atravessam semanas. Gravação POR ITEM, não
# pelo acervo inteiro: dois operadores editando pontos diferentes do mesmo
# cliente não se sobrescrevem.
# ---------------------------------------------------------------------------
@router.get("/acervo")
def obter_acervo(cliente: str) -> dict:
    """Devolve { pontos, anormalidades } de um cliente."""
    estado = _ler(DADOS_PATH, ESTRUTURA_VAZIA)
    return estado["acervo"].get(cliente, {"pontos": [], "anormalidades": []})


@router.post("/acervo")
def salvar_item_acervo(payload: SalvarItemAcervoPayload) -> dict:
    """
    Insere ou atualiza UM item do acervo. Se item.id vier vazio, gera um novo.

    Coerência automática entre status e data_resolucao: marcar "Resolvido"
    sem data carimba hoje; sair de "Resolvido" limpa a data. Isso evita o
    estado inconsistente (status resolvido + data nula) que quebraria o
    filtro por período.
    """
    item = dict(payload.item)

    if not item.get("id"):
        item["id"] = uuid.uuid4().hex[:12]

    if item.get("status") == "Resolvido" and not item.get("data_resolucao"):
        item["data_resolucao"] = date.today().isoformat()
    if item.get("status") != "Resolvido":
        item["data_resolucao"] = None

    # valida o formato conforme o tipo
    modelo = PontoAtencao if payload.tipo == "pontos" else AnormalidadeTecnica
    try:
        item = modelo(**item).model_dump()
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Item inválido: {e}")

    with _lock:
        estado = _ler(DADOS_PATH, ESTRUTURA_VAZIA)

        if payload.cliente not in estado["clientes"]:
            estado["clientes"].append(payload.cliente)

        estado["acervo"].setdefault(payload.cliente, {"pontos": [], "anormalidades": []})
        lista = estado["acervo"][payload.cliente].setdefault(payload.tipo, [])

        idx = next((i for i, x in enumerate(lista) if x.get("id") == item["id"]), None)
        if idx is None:
            lista.append(item)
        else:
            lista[idx] = item

        _escrever_atomico(estado, DADOS_PATH)

    return {"ok": True, "id": item["id"], "tipo": payload.tipo}


@router.delete("/acervo")
def apagar_item_acervo(cliente: str, tipo: str, id: str) -> dict:
    if tipo not in ("pontos", "anormalidades"):
        raise HTTPException(status_code=400, detail="tipo deve ser 'pontos' ou 'anormalidades'")
    with _lock:
        estado = _ler(DADOS_PATH, ESTRUTURA_VAZIA)
        lista = estado["acervo"].get(cliente, {}).get(tipo, [])
        antes = len(lista)
        estado["acervo"].setdefault(cliente, {}).setdefault(tipo, [])
        estado["acervo"][cliente][tipo] = [x for x in lista if x.get("id") != id]
        if len(estado["acervo"][cliente][tipo]) != antes:
            _escrever_atomico(estado, DADOS_PATH)
            return {"ok": True, "removido": True}
        return {"ok": True, "removido": False}


# ---------------------------------------------------------------------------
# ROTA — relatório: a semana + os itens do acervo vivos naquele período
# ---------------------------------------------------------------------------
def _itens_do_periodo(itens: list, ini: str, fim: str) -> list:
    """
    Um item entra no relatório da semana [ini..fim] se sua janela de vida
    se sobrepõe ao período:

        identificado ATÉ o fim da semana   E   (ainda aberto OU resolvido
                                                a partir do início da semana)

    Consequências desejadas:
      - ponto aberto na semana 1 aparece nas semanas 1, 2, 3... até resolver
      - ponto resolvido na quarta aparece na semana daquela quarta, marcado
        como resolvido (é o "indicador visual de resolução" do requisito)
      - ponto resolvido na semana passada NÃO polui o relatório desta
      - ponto identificado no futuro não vaza para relatórios anteriores
    """
    out = []
    for it in itens:
        ident = it.get("data_identificacao")
        if not ident or ident > fim:
            continue
        resol = it.get("data_resolucao")
        if resol and resol < ini:
            continue
        out.append({**it, "resolvido_no_periodo": bool(resol and ini <= resol <= fim)})
    return out


@router.get("/relatorio")
def montar_relatorio(cliente: str, semana: str) -> dict:
    """
    Devolve tudo que o PDF de uma semana precisa, já filtrado.

    O front NÃO reimplementa o filtro do acervo: a regra de sobreposição
    mora aqui, num lugar só.
    """
    semana = _validar_segunda(semana)
    fim = _domingo_da_semana(semana)

    estado = _ler(DADOS_PATH, ESTRUTURA_VAZIA)
    cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)

    dados_semana = estado["semanas"].get(cliente, {}).get(semana, {})
    acervo_cli = estado["acervo"].get(cliente, {"pontos": [], "anormalidades": []})

    ficha = next((c for c in cad["clientes"] if c.get("codigo") == cliente), None)

    pontos = _itens_do_periodo(acervo_cli.get("pontos", []), semana, fim)
    anormalidades = _itens_do_periodo(acervo_cli.get("anormalidades", []), semana, fim)

    armes = dados_semana.get("armes", [])
    desarmes = dados_semana.get("desarmes", [])
    disparos = dados_semana.get("disparos", [])
    zonas = dados_semana.get("zonas", [])
    visitas = dados_semana.get("visitas", [])

    return {
        "cliente": ficha or {"codigo": cliente, "nome": cliente},
        "periodo": {
            "inicio": semana,
            "fim": fim,
            "semana_do_mes": _semana_do_mes(semana),
            "mes_referencia": _mes_referencia(semana, fim),
        },
        "armes": armes,
        "desarmes": desarmes,
        "disparos": disparos,
        "zonas": zonas,
        "visitas": visitas,
        "pontos": pontos,
        "anormalidades": anormalidades,
        "observacoes": dados_semana.get("observacoes", ""),
        "resumo": {
            "armes": len(armes),
            "desarmes": len(desarmes),
            "disparos": len(disparos),
            "zonas": len(zonas),
            "visitas": len(visitas),
            "pontos_abertos": sum(1 for p in pontos if not p.get("data_resolucao")),
        },
    }


MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
         "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _semana_do_mes(segunda_iso: str) -> int:
    """
    Rótulo ordinal como no deck real ("SEMANA 02/JULHO").

    A semana pertence ao mês da sua QUINTA-FEIRA (regra ISO 8601): assim a
    semana de 29/06 a 05/07 é a semana 1 de julho, não a semana 5 de junho.
    Sem isso, semanas a cavalo entre meses recebem rótulo ambíguo.
    """
    d = date.fromisoformat(segunda_iso)
    quinta = d + timedelta(days=3)
    primeiro = date(quinta.year, quinta.month, 1)
    # segunda da semana que contém o dia 1º do mês
    primeira_segunda = primeiro - timedelta(days=primeiro.weekday())
    # essa semana só conta como "semana 1" se sua quinta cair dentro do mês
    if primeira_segunda + timedelta(days=3) < primeiro:
        primeira_segunda += timedelta(days=7)
    return ((d - primeira_segunda).days // 7) + 1


def _mes_referencia(segunda_iso: str, domingo_iso: str) -> str:
    d = date.fromisoformat(segunda_iso)
    quinta = d + timedelta(days=3)
    return f"{MESES[quinta.month - 1].capitalize()}/{quinta.year}"


# ---------------------------------------------------------------------------
# ROTAS — cadastros (clientes / usuários / sensores / dispositivos)
#
# Arquivo separado, como no frota: ciclo de vida próprio, mudam pouco, e não
# disputam lock com o salvamento de uma semana.
# ---------------------------------------------------------------------------
@router.get("/cadastros")
def obter_cadastros() -> dict:
    cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)
    if not cad.get("dispositivos"):
        cad["dispositivos"] = list(DISPOSITIVOS_PADRAO)
    return cad


@router.post("/cadastros")
def salvar_cadastros(payload: CadastrosPayload) -> dict:
    """
    Substitui o bloco inteiro — mantida só para backup/importação em massa.

    NÃO é mais usada pelo front no fluxo normal de edição (ver rotas
    incrementais abaixo). Substituir o objeto inteiro é seguro só com um
    único operador editando por vez; com dois operadores em telas abertas
    ao mesmo tempo, quem salva por último apaga silenciosamente o que o
    outro tinha acabado de gravar — foi exatamente o bug relatado pelo
    Lázaro em 23/09/2026 ("editar qualquer informação de nome, dispositivo,
    sensor etc não pode apagar o que já foi feito").
    """
    with _lock_cad:
        novo = {
            "versao": 1,
            "clientes": payload.clientes,
            "usuarios": payload.usuarios,
            "sensores": payload.sensores,
            "dispositivos": payload.dispositivos or list(DISPOSITIVOS_PADRAO),
        }
        _escrever_atomico(novo, CADASTROS_PATH)
    return {
        "ok": True,
        "clientes": len(novo["clientes"]),
        "sensores": sum(len(v) for v in novo["sensores"].values()),
    }


# ---------------------------------------------------------------------------
# ROTAS INCREMENTAIS DE CADASTRO — correção do bug acima (Lázaro, 23/09/2026)
#
# Cada rota altera SÓ o item editado, nunca o objeto inteiro — mesmo padrão
# já usado no frota_routes.py (upsert com "_antigo" opcional pra permitir
# renomear sem duplicar nem perder o item). O front (Cadastros.jsx a partir
# da v3) já foi atualizado para chamar estas rotas em vez de POST /cadastros.
# ---------------------------------------------------------------------------
class ClientePayload(BaseModel):
    codigo: str
    nome: str = ""
    endereco: str = ""
    cidade: str = ""
    estado: str = ""
    cep: str = ""
    responsavel: str = ""
    codigo_antigo: Optional[str] = None  # preenchido ao renomear o código


class UsuarioPayload(BaseModel):
    cliente: str          # código do cliente dono do usuário
    nome: str
    cargo: str = ""
    nome_antigo: Optional[str] = None


class SensorPayload(BaseModel):
    cliente: str
    nome: str
    tipo: str = ""
    localizacao: str = ""
    zona: str = ""
    status: str = "Ativo"
    obs: str = ""
    nome_antigo: Optional[str] = None


class DispositivoPayload(BaseModel):
    nome: str
    nome_antigo: Optional[str] = None


# --- clientes -----------------------------------------------------------
@router.post("/cadastros/cliente")
def upsert_cliente(p: ClientePayload) -> dict:
    codigo = p.codigo.strip()
    if not codigo:
        raise HTTPException(status_code=400, detail="Código do cliente vazio.")
    novo = {
        "codigo": codigo, "nome": p.nome, "endereco": p.endereco,
        "cidade": p.cidade, "estado": p.estado, "cep": p.cep,
        "responsavel": p.responsavel,
    }
    with _lock_cad:
        cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)
        clientes = cad["clientes"]
        acha = lambda c: next((i for i, x in enumerate(clientes) if x.get("codigo") == c), None)

        if p.codigo_antigo is None:
            if acha(codigo) is not None:
                raise HTTPException(status_code=409, detail="Já existe um cliente com esse código.")
            clientes.append(novo)
        else:
            idx = acha(p.codigo_antigo.strip())
            if idx is None:
                raise HTTPException(status_code=404, detail="Cliente não encontrado para edição.")
            colisao = acha(codigo)
            if colisao is not None and colisao != idx:
                raise HTTPException(status_code=409, detail="Já existe um cliente com esse código.")
            clientes[idx] = novo
            # renomeou o código? propaga pras chaves de usuarios/sensores
            if p.codigo_antigo != codigo:
                for eixo in ("usuarios", "sensores"):
                    if p.codigo_antigo in cad[eixo]:
                        cad[eixo][codigo] = cad[eixo].pop(p.codigo_antigo)

        _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "clientes": len(cad["clientes"])}


@router.delete("/cadastros/cliente")
def del_cliente(codigo: str) -> dict:
    with _lock_cad:
        cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)
        antes = len(cad["clientes"])
        cad["clientes"] = [c for c in cad["clientes"] if c.get("codigo") != codigo]
        if len(cad["clientes"]) != antes:
            _escrever_atomico(cad, CADASTROS_PATH)
            return {"ok": True, "removido": True}
        return {"ok": True, "removido": False}


# --- usuários -------------------------------------------------------------
@router.post("/cadastros/usuario")
def upsert_usuario(p: UsuarioPayload) -> dict:
    nome = p.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome do usuário vazio.")
    novo = {"nome": nome, "cargo": p.cargo}

    with _lock_cad:
        cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)
        lista = cad["usuarios"].setdefault(p.cliente, [])
        acha = lambda n: next((i for i, u in enumerate(lista) if u["nome"].casefold() == n.casefold()), None)

        if p.nome_antigo is None:
            if acha(nome) is not None:
                raise HTTPException(status_code=409, detail="Já existe um usuário com esse nome para este cliente.")
            lista.append(novo)
        else:
            idx = acha(p.nome_antigo.strip())
            if idx is None:
                raise HTTPException(status_code=404, detail="Usuário não encontrado para edição.")
            colisao = acha(nome)
            if colisao is not None and colisao != idx:
                raise HTTPException(status_code=409, detail="Já existe um usuário com esse nome para este cliente.")
            lista[idx] = novo

        _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "usuarios": lista}


@router.delete("/cadastros/usuario")
def del_usuario(cliente: str, nome: str) -> dict:
    with _lock_cad:
        cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)
        lista = cad["usuarios"].get(cliente, [])
        antes = len(lista)
        cad["usuarios"][cliente] = [u for u in lista if u["nome"].casefold() != nome.casefold()]
        if len(cad["usuarios"][cliente]) != antes:
            _escrever_atomico(cad, CADASTROS_PATH)
            return {"ok": True, "removido": True}
        return {"ok": True, "removido": False}


# --- sensores ---------------------------------------------------------------
@router.post("/cadastros/sensor")
def upsert_sensor(p: SensorPayload) -> dict:
    nome = p.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome do sensor vazio.")
    novo = {
        "nome": nome, "tipo": p.tipo, "localizacao": p.localizacao,
        "zona": p.zona, "status": p.status, "obs": p.obs,
    }

    with _lock_cad:
        cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)
        lista = cad["sensores"].setdefault(p.cliente, [])
        acha = lambda n: next((i for i, s in enumerate(lista) if s["nome"].casefold() == n.casefold()), None)

        if p.nome_antigo is None:
            if acha(nome) is not None:
                raise HTTPException(status_code=409, detail="Já existe um sensor com esse nome para este cliente.")
            lista.append(novo)
        else:
            idx = acha(p.nome_antigo.strip())
            if idx is None:
                raise HTTPException(status_code=404, detail="Sensor não encontrado para edição.")
            colisao = acha(nome)
            if colisao is not None and colisao != idx:
                raise HTTPException(status_code=409, detail="Já existe um sensor com esse nome para este cliente.")
            lista[idx] = novo

        _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "sensores": lista}


@router.delete("/cadastros/sensor")
def del_sensor(cliente: str, nome: str) -> dict:
    with _lock_cad:
        cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)
        lista = cad["sensores"].get(cliente, [])
        antes = len(lista)
        cad["sensores"][cliente] = [s for s in lista if s["nome"].casefold() != nome.casefold()]
        if len(cad["sensores"][cliente]) != antes:
            _escrever_atomico(cad, CADASTROS_PATH)
            return {"ok": True, "removido": True}
        return {"ok": True, "removido": False}


# --- dispositivos -----------------------------------------------------------
@router.post("/cadastros/dispositivo")
def upsert_dispositivo(p: DispositivoPayload) -> dict:
    nome = p.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome do dispositivo vazio.")

    with _lock_cad:
        cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)
        disp = cad.get("dispositivos") or list(DISPOSITIVOS_PADRAO)
        acha = lambda n: next((i for i, d in enumerate(disp) if d.casefold() == n.casefold()), None)

        if p.nome_antigo is None:
            if acha(nome) is not None:
                raise HTTPException(status_code=409, detail="Já existe um dispositivo com esse nome.")
            disp.append(nome)
        else:
            idx = acha(p.nome_antigo.strip())
            if idx is None:
                raise HTTPException(status_code=404, detail="Dispositivo não encontrado para edição.")
            colisao = acha(nome)
            if colisao is not None and colisao != idx:
                raise HTTPException(status_code=409, detail="Já existe um dispositivo com esse nome.")
            disp[idx] = nome

        cad["dispositivos"] = disp
        _escrever_atomico(cad, CADASTROS_PATH)
    return {"ok": True, "dispositivos": cad["dispositivos"]}


@router.delete("/cadastros/dispositivo")
def del_dispositivo(nome: str) -> dict:
    with _lock_cad:
        cad = _ler(CADASTROS_PATH, CADASTROS_VAZIO)
        disp = cad.get("dispositivos") or list(DISPOSITIVOS_PADRAO)
        antes = len(disp)
        cad["dispositivos"] = [d for d in disp if d.casefold() != nome.casefold()]
        if len(cad["dispositivos"]) != antes:
            _escrever_atomico(cad, CADASTROS_PATH)
            return {"ok": True, "removido": True}
        return {"ok": True, "removido": False}
