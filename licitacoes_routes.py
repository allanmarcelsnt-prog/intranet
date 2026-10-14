"""
licitacoes_routes.py — Montagem de Proposta (setor de licitações).

Registro no main.py — ACIMA do app.mount("/", StaticFiles(...)):

    from licitacoes_routes import router as licitacoes_router
    app.include_router(licitacoes_router)

Segue o padrão dos apps da intranet (frota_routes.py / monitoramento_routes.py):
  BASE = Path(__file__).parent · data/ · escrita atômica (tmp+rename) · lock
  rotas por INTENÇÃO (não por estado), para acessos simultâneos somarem.

Autenticação: usa a dependência usuario_logado do auth_routes — as ações
gravam o usuário AUTENTICADO (não um nome vindo do cliente), base da auditoria.
"""
import io
import json
import os
import smtplib
import ssl
import tempfile
import threading
import time
import zipfile
from copy import copy
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from auth_routes import usuario_logado
import auth_store

router = APIRouter(prefix="/api/licitacoes", tags=["licitacoes"])

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO — mesmo padrão do frota_routes.py (BASE = Path(__file__).parent)
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent
PREGOES_PATH = BASE / "data" / "licitacoes_pregoes.json"
AUDIT_PATH = BASE / "data" / "licitacoes_auditoria.json"
LICOES_PATH = BASE / "data" / "licitacoes_licoes.json"
HISTORICO_PATH = BASE / "data" / "licitacoes_historico.json"
MODELO_EFFECTI = BASE / "data" / "modelo_effecti.xlsx"
DATASHEETS_DIR = BASE / "data" / "datasheets"
DOCS_DIR = BASE / "data" / "documentos"
EMAIL_LOG_PATH = BASE / "data" / "licitacoes_email_log.json"
SLA_LOG_PATH = BASE / "data" / "licitacoes_sla_log.json"

PREGOES_VAZIO = []
AUDIT_VAZIO = {"etapas": [], "log": []}
LICOES_VAZIO = []
HISTORICO_VAZIO = []
EMAIL_LOG_VAZIO = []
SLA_LOG_VAZIO = []

_lock = threading.Lock()
_email_lock = threading.Lock()
_sla_lock = threading.Lock()

# ---------------------------------------------------------------------------
# E-MAIL — infraestrutura de envio (Fatia 1 do sistema de notificação).
# Credenciais via variável de ambiente (mesmo padrão do INTRANET_JWT_SECRET);
# sem elas, o app funciona normal — só não manda e-mail (nada trava, nunca
# falha silenciosamente: toda tentativa vai pro log em EMAIL_LOG_PATH).
# ---------------------------------------------------------------------------
SMTP_HOST = os.environ.get("INTRANET_SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("INTRANET_SMTP_PORT", "587") or "587")
SMTP_USER = os.environ.get("INTRANET_SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("INTRANET_SMTP_PASS", "")
# STARTTLS (porta 587, padrão) vs SSL direto (porta 465, Locaweb costuma aceitar as duas)
SMTP_STARTTLS = os.environ.get("INTRANET_SMTP_STARTTLS", "true").strip().lower() not in ("false", "0", "nao", "não")
SMTP_FROM = os.environ.get("INTRANET_SMTP_FROM", "").strip() or SMTP_USER
SMTP_FROM_NOME = os.environ.get("INTRANET_SMTP_FROM_NOME", "Gestor de Pregões — Safenet")
INTRANET_BASE_URL = os.environ.get("INTRANET_BASE_URL", "http://intranet.safenet.lan:8081/licitacoes/").strip()


def _smtp_configurado() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def _registrar_email_log(destinatarios, assunto, evento, ok, erro=None):
    with _email_lock:
        log = _ler(EMAIL_LOG_PATH, EMAIL_LOG_VAZIO)
        log.append({
            "em": AGORA(), "evento": evento, "destinatarios": destinatarios,
            "assunto": assunto, "ok": ok, "erro": erro,
        })
        if len(log) > 500:  # não deixa crescer sem limite
            log = log[-500:]
        _escrever_atomico(log, EMAIL_LOG_PATH)


def _montar_html_email(titulo: str, corpo_html: str, cta_texto: str = None, cta_url: str = None) -> str:
    """Monta o HTML do e-mail com a identidade visual Safenet (verde/navy)."""
    cta = ""
    if cta_texto and cta_url:
        cta = f'''<tr><td style="padding:22px 32px 6px">
          <a href="{cta_url}" style="display:inline-block;background:#7AC143;color:#ffffff;
             text-decoration:none;font-weight:700;font-size:14px;padding:11px 22px;
             border-radius:8px;font-family:Arial,Helvetica,sans-serif">{cta_texto}</a>
        </td></tr>'''
    return f'''<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background:#f2f4f3;font-family:Arial,Helvetica,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f3;padding:28px 0">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e6e3;max-width:92vw">
<tr><td style="background:#164023;padding:20px 32px">
  <span style="color:#ffffff;font-size:16px;font-weight:800;letter-spacing:.3px">REGGRA</span>
  <span style="color:#bcd9c7;font-size:12px;margin-left:8px">Gestor de Pregões · Safenet</span>
</td></tr>
<tr><td style="padding:28px 32px 4px">
  <h1 style="margin:0 0 14px;font-size:18px;color:#164023;font-weight:800">{titulo}</h1>
  <div style="font-size:14px;line-height:1.55;color:#333333">{corpo_html}</div>
</td></tr>
{cta}
<tr><td style="padding:22px 32px 28px">
  <div style="border-top:1px solid #e2e6e3;padding-top:16px;font-size:11.5px;color:#8a9aab">
    Este e-mail foi gerado automaticamente pelo Gestor de Pregões. Não responda — em caso de
    dúvida, fale com a Coordenação de Licitações.
  </div>
</td></tr>
</table>
</td></tr>
</table>
</body></html>'''


def enviar_email(destinatarios, assunto: str, titulo: str, corpo_html: str, evento: str,
                  cta_texto: str = None, cta_url: str = None) -> bool:
    """Tenta enviar um e-mail. NUNCA levanta exceção pro chamador — se o SMTP
    não estiver configurado, ou a conexão falhar, apenas registra no log e
    retorna False. Uma ação do usuário nunca deve travar ou quebrar por causa
    de e-mail; use sempre via BackgroundTasks nas rotas, nunca sync no meio
    de uma requisição."""
    destinatarios = sorted({d.strip() for d in (destinatarios or []) if d and "@" in d})
    if not destinatarios:
        return False
    if not _smtp_configurado():
        _registrar_email_log(destinatarios, assunto, evento, False,
                              "SMTP não configurado (variáveis de ambiente ausentes)")
        return False
    html = _montar_html_email(titulo, corpo_html, cta_texto, cta_url)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = formataddr((SMTP_FROM_NOME, SMTP_FROM))
    msg["To"] = ", ".join(destinatarios)
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        if SMTP_STARTTLS:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_FROM, destinatarios, msg.as_string())
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=15) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_FROM, destinatarios, msg.as_string())
        _registrar_email_log(destinatarios, assunto, evento, True)
        return True
    except Exception as e:
        _registrar_email_log(destinatarios, assunto, evento, False, str(e))
        return False


@router.get("/admin/email/status")
def email_status(user=Depends(usuario_logado)):
    if user.get("papel") != "coordenador":
        raise HTTPException(status_code=403, detail="Apenas coordenador pode ver a configuração de e-mail.")
    return {
        "configurado": _smtp_configurado(),
        "host": SMTP_HOST or None,
        "porta": SMTP_PORT,
        "remetente": SMTP_FROM or None,
    }


class TesteEmailIn(BaseModel):
    destinatario: str

@router.post("/admin/email/teste")
def email_teste(dados: TesteEmailIn, user=Depends(usuario_logado)):
    if user.get("papel") != "coordenador":
        raise HTTPException(status_code=403, detail="Apenas coordenador pode testar o envio de e-mail.")
    dest = (dados.destinatario or "").strip()
    if not dest or "@" not in dest:
        raise HTTPException(status_code=400, detail="Informe um e-mail válido.")
    ok = enviar_email(
        destinatarios=[dest],
        assunto="Teste — Gestor de Pregões (Reggra)",
        titulo="Deu certo! ✅",
        corpo_html=(
            f"<p>Este é um e-mail de teste disparado por <b>{user['nome']}</b> pelo painel "
            f"de configuração de e-mail do Gestor de Pregões.</p>"
            f"<p>Se você recebeu esta mensagem, o envio via SMTP está funcionando corretamente.</p>"
        ),
        evento="teste_manual",
    )
    if ok:
        return {"ok": True}
    if not _smtp_configurado():
        return {"ok": False, "erro": "SMTP não configurado no servidor (variáveis de ambiente ausentes)."}
    # tenta achar o motivo mais recente no log pra devolver pro usuário
    log = _ler(EMAIL_LOG_PATH, EMAIL_LOG_VAZIO)
    ultimo = log[-1] if log else None
    return {"ok": False, "erro": (ultimo or {}).get("erro") or "Falha desconhecida ao enviar."}


@router.get("/admin/email/log")
def email_log(user=Depends(usuario_logado)):
    if user.get("papel") != "coordenador":
        raise HTTPException(status_code=403, detail="Apenas coordenador pode ver o log de e-mail.")
    log = _ler(EMAIL_LOG_PATH, EMAIL_LOG_VAZIO)
    return {"log": log[-100:]}  # últimos 100, mais recente por último (front inverte pra exibir)


# ---------------------------------------------------------------------------
# Destinatários — traduz "responsável" (string, pode ser combinada tipo
# "Rafael / Pedro") e papéis de etapa em e-mails cadastrados. Usado pelos
# gatilhos reativos (Fatia 3) e futuramente pela rotina de SLA.
# ---------------------------------------------------------------------------
def _emails_por_nomes(nomes):
    if not nomes:
        return []
    mapa = {u["nome"]: u.get("email") for u in auth_store.listar_emails()}
    return [mapa[n] for n in nomes if mapa.get(n)]

def _emails_do_responsavel(p):
    """Divide o campo 'responsavel' do pregão (pode vir combinado, ex.
    'Rafael / Pedro' — regra automática de atribuição) e devolve o e-mail
    de cada um que tiver e-mail cadastrado."""
    resp = (p.get("responsavel") or "").strip()
    if not resp:
        return []
    nomes = [n.strip() for n in resp.split("/") if n.strip()]
    return _emails_por_nomes(nomes)

def _emails_coordenadores():
    return [u["email"] for u in auth_store.listar_emails()
            if u.get("papel") == "coordenador" and u.get("email")]

def _emails_por_papel(papeis):
    if not papeis:
        return []
    return [u["email"] for u in auth_store.listar_emails()
            if u.get("papel") in papeis and u.get("email")]

STATUS_ROTULO = {
    "triagem": "Triagem", "aguardando_adriano": "Aguardando Adriano",
    "aguardando_rodrigo": "Triado · aguardando Rodrigo", "definir_sugestoes": "Definir sugestões",
    "em_cotacao": "Em cotação", "em_revisao": "Em revisão", "pronto_effecti": "Pronto p/ Effecti",
    "em_disputa": "Em disputa", "ganho": "Ganho", "perdido": "Perdido", "nao_realizado": "Não realizado",
}


# ---------------------------------------------------------------------------
# SLA diário (Fatia 4) — avisa quando um pregão fica parado tempo demais numa
# etapa, ou quando uma devolução não é corrigida. Roda 1x por dia sozinho
# (thread de fundo abaixo), repetindo o aviso todo dia até o pregão sair
# daquela situação. Idempotente: 'licitacoes_sla_log.json' guarda o que já
# foi avisado HOJE, pra não duplicar se o processo reiniciar no meio do dia.
# ---------------------------------------------------------------------------
SLA_VALIDACAO_DIAS = 3   # parado em "Aguardando Adriano"
SLA_DEVOLUCAO_DIAS = 1   # devolvido e ainda não corrigido

def _dias_desde(data_str):
    if not data_str:
        return None
    try:
        dt = datetime.strptime(data_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    return (datetime.now() - dt).days

def _hoje_str():
    return datetime.now().strftime("%Y-%m-%d")

def _ja_avisado_hoje(pregao_id, tipo):
    log = _ler(SLA_LOG_PATH, SLA_LOG_VAZIO)
    hoje = _hoje_str()
    return any(l.get("pregao_id") == pregao_id and l.get("tipo") == tipo and l.get("dia") == hoje for l in log)

def _marcar_avisado_hoje(pregao_id, tipo):
    with _sla_lock:
        log = _ler(SLA_LOG_PATH, SLA_LOG_VAZIO)
        log.append({"pregao_id": pregao_id, "tipo": tipo, "dia": _hoje_str(), "em": AGORA()})
        if len(log) > 3000:
            log = log[-3000:]
        _escrever_atomico(log, SLA_LOG_PATH)

def _entrada_aberta_etapa(pregao_id, etapa):
    """Acha a entrada de auditoria EM ABERTO (fim=None) dessa etapa/pregão —
    é o registro de quando o pregão entrou na etapa atual."""
    a = _ler(AUDIT_PATH, AUDIT_VAZIO)
    for e in reversed(a.get("etapas", [])):
        if e.get("pregao_id") == pregao_id and e.get("etapa") == etapa and e.get("fim") is None:
            return e
    return None

def _rodar_scan_sla():
    """Varre os pregões ativos e dispara os avisos de SLA que ainda não
    foram mandados hoje. Chamada 1x/dia pelo loop de fundo, ou manualmente
    via POST /admin/sla/rodar-agora."""
    pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
    for p in pregoes:
        if p.get("arquivado"):
            continue          # card arquivado não gera aviso de SLA
        pid = p.get("id")
        status = p.get("status")

        # --- SLA 1: parado em "Aguardando Adriano" há dias demais ---
        if status == "aguardando_adriano" and not _ja_avisado_hoje(pid, "validacao_parada"):
            entrada = _entrada_aberta_etapa(pid, "validacao")
            dias = _dias_desde(entrada["inicio"]) if entrada else None
            if dias is not None and dias >= SLA_VALIDACAO_DIAS:
                destinatarios = _emails_coordenadores()
                if destinatarios:
                    enviar_email(
                        destinatarios=destinatarios,
                        assunto=f"⏱ Parado há {dias} dia(s) em validação — {p.get('orgao')} PE {p.get('numero')}",
                        titulo="Pregão parado esperando validação",
                        corpo_html=(
                            f"<p><b>{p.get('orgao')}</b><br>PE {p.get('numero')}</p>"
                            f"<p>Está em <b>Aguardando Adriano</b> há <b>{dias} dia(s)</b> sem avançar.</p>"
                        ),
                        evento="sla_validacao_parada",
                        cta_texto="Abrir o Gestor de Pregões", cta_url=INTRANET_BASE_URL,
                    )
                _marcar_avisado_hoje(pid, "validacao_parada")

        # --- SLA 2: devolução sem correção há dias demais ---
        dev = p.get("devolucao")
        if dev and not _ja_avisado_hoje(pid, "devolucao_parada"):
            dias = _dias_desde(dev.get("em"))
            if dias is not None and dias >= SLA_DEVOLUCAO_DIAS:
                para_status = dev.get("para_status")
                destinatarios = _emails_do_responsavel(p) if para_status == "em_cotacao" else []
                if not destinatarios:
                    destinatarios = _emails_por_papel(PAPEL_DA_ETAPA.get(ETAPA_DE.get(para_status), set()))
                destinatarios = sorted(set(destinatarios) | set(_emails_coordenadores()))
                if destinatarios:
                    motivo_html = (dev.get("motivo") or "").replace("<", "&lt;")
                    enviar_email(
                        destinatarios=destinatarios,
                        assunto=f"⤺ Devolução sem correção há {dias} dia(s) — {p.get('orgao')} PE {p.get('numero')}",
                        titulo="Um pregão devolvido ainda não foi corrigido",
                        corpo_html=(
                            f"<p><b>{p.get('orgao')}</b><br>PE {p.get('numero')}</p>"
                            f"<p>Foi devolvido por {dev.get('por','—')} há <b>{dias} dia(s)</b> e ainda não avançou. "
                            f"Etapa atual: <b>{STATUS_ROTULO.get(para_status, para_status)}</b>. Motivo:</p>"
                            f"<p style=\"background:#fdf2f2;border-left:3px solid #b23b3b;padding:10px 14px;"
                            f"border-radius:6px;margin:10px 0\">{motivo_html}</p>"
                        ),
                        evento="sla_devolucao_parada",
                        cta_texto="Abrir o Gestor de Pregões", cta_url=INTRANET_BASE_URL,
                    )
                _marcar_avisado_hoje(pid, "devolucao_parada")

def _loop_sla_diario():
    """Thread de fundo: acorda de hora em hora e roda o scan de SLA uma vez
    por dia — não precisa de horário exato, só de rodar ao menos 1x/dia (a
    primeira checagem depois da virada do dia já dispara, inclusive logo
    após o container subir). Nunca deixa um erro pontual matar o loop."""
    ultimo_dia = None
    while True:
        try:
            hoje = _hoje_str()
            if hoje != ultimo_dia:
                _rodar_scan_sla()
                ultimo_dia = hoje
        except Exception:
            pass
        time.sleep(3600)

# OBS: a thread só é iniciada no FINAL deste arquivo (ver
# "threading.Thread(target=_loop_sla_diario, ...).start()" na última linha),
# de propósito — assim ela só começa a rodar depois que TODO o módulo já
# terminou de carregar (PAPEL_DA_ETAPA, ETAPA_DE, todas as rotas etc.),
# eliminando qualquer risco de corrida com nomes ainda não definidos.


@router.post("/admin/sla/rodar-agora")
def sla_rodar_agora(user=Depends(usuario_logado)):
    """Dispara o scan de SLA na hora, sem esperar o próximo ciclo horário.
    Útil pra testar depois de implantar, ou se um coordenador quiser forçar
    a checagem. Continua respeitando o 'já avisado hoje' (não duplica)."""
    if user.get("papel") != "coordenador":
        raise HTTPException(status_code=403, detail="Apenas coordenador pode rodar o SLA manualmente.")
    _rodar_scan_sla()
    return {"ok": True}

AGORA = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")

STATUS = [
    "triagem", "aguardando_adriano", "aguardando_rodrigo", "definir_sugestoes",
    "em_cotacao", "em_revisao", "pronto_effecti", "em_disputa",
    "ganho", "perdido", "nao_realizado",
]
ETAPA_DE = {
    "triagem": "triagem", "aguardando_adriano": "validacao", "aguardando_rodrigo": "atribuicao",
    "definir_sugestoes": "definir_sugestoes", "em_cotacao": "cotacao", "em_revisao": "revisao",
    "pronto_effecti": "pronto", "em_disputa": "disputa",
}

# Sequência linear do fluxo (para o coordenador "retroceder" um passo). Só cobre
# o caminho principal; ramos finais (ganho/perdido/nao_realizado) não retrocedem.
SEQ_STATUS = [
    "triagem", "aguardando_adriano", "aguardando_rodrigo", "definir_sugestoes",
    "em_cotacao", "em_revisao", "pronto_effecti", "em_disputa",
]

# Papéis que "possuem" cada etapa. O coordenador SEMPRE pode agir em qualquer
# etapa (meio-termo): o processo é fiel ao papel, mas o coordenador não fica
# engessado quando precisa intervir. A auditoria continua gravando quem agiu.
PAPEL_DA_ETAPA = {
    "triagem":         {"analista", "analista_cotacao"},
    "validacao":       {"comercial"},
    "atribuicao":      {"atribuidor"},
    "definir_sugestoes": {"coordenador"},   # etapa do Allan (coordenador)
    "cotacao":         {"operacional", "analista_cotacao"},
    "revisao":         {"coordenador"},      # revisão final é do coordenador
    "entrega":         {"analista", "analista_cotacao"},  # pegam a planilha p/ o Effecti
    "disputa":         {"analista", "analista_cotacao"},  # mover p/ disputa e registrar resultado
}

def exige_papel(user: dict, etapa: str):
    """
    Garante que o usuário pode agir nesta etapa: precisa ter o papel dono da
    etapa OU ser coordenador (que pode tudo). Caso contrário, 403.
    """
    papel = user.get("papel")
    if papel == "coordenador":
        return
    donos = PAPEL_DA_ETAPA.get(etapa, set())
    if papel not in donos:
        rotulo = " ou ".join(sorted(donos)) or "responsável"
        raise HTTPException(
            status_code=403,
            detail=f"Esta etapa é do {rotulo}. Seu perfil ({papel}) não pode concluí-la.",
        )


def exige_cotacao(user: dict, pregao: dict):
    """Permissão da cotação: operacional, analista_cotacao e coordenador sempre
    podem. O analista (triagem) pode cotar SÓ quando o pregão é de fornecimento
    e todos os itens têm modelo de referência."""
    papel = user.get("papel")
    if papel in ("operacional", "analista_cotacao", "coordenador"):
        return
    if papel == "analista" and analista_pode_cotar(pregao):
        return
    raise HTTPException(
        status_code=403,
        detail="A cotação é da equipe operacional. Analista (triagem) só cota fornecimento com todos os itens referenciados.",
    )


# ---------------------------------------------------------------------------
# Regra de roteamento da atribuição (etapa do Rodrigo)
# ---------------------------------------------------------------------------
# A regra decide sozinha por TIPO + REFERÊNCIA e o pregão segue sem confirmação.
# O Rodrigo (ou coordenador) só ajusta exceção pela tela.
#   fornecimento COM referência  -> Rafael/Pedro cotam  -> em_cotacao
#   fornecimento SEM referência  -> Allan define 3       -> definir_sugestoes
#   locação / serviço            -> Osvair (apoio Allan) -> em_cotacao
def rotear_atribuicao(pregao: dict) -> dict:
    tipo = (pregao.get("tipo") or "fornecimento").lower()
    tem_ref = bool(pregao.get("tem_referencia"))
    if tipo in ("locacao", "servico"):
        return {"responsavel": "Osvair", "apoio": "Allan Marcel",
                "proximo_status": "em_cotacao",
                "motivo": "Locação/serviço → Osvair (com apoio do Allan)."}
    # fornecimento
    if tem_ref:
        return {"responsavel": "Rafael / Pedro", "apoio": None,
                "proximo_status": "em_cotacao",
                "motivo": "Fornecimento com modelo de referência → Rafael/Pedro cotam direto."}
    return {"responsavel": "Allan Marcel", "apoio": None,
            "proximo_status": "definir_sugestoes",
            "motivo": "Fornecimento sem referência → Allan define 3 sugestões antes da cotação."}


def analista_pode_cotar(pregao: dict) -> bool:
    """Analista (Thiago/Luana) pode cotar SÓ quando o pregão é de fornecimento
    E todos os itens têm modelo de referência (item a item)."""
    tipo = (pregao.get("tipo") or "fornecimento").lower()
    itens = pregao.get("itens", [])
    if tipo != "fornecimento" or not itens:
        return False
    return all((it.get("modelo_referencia") or "").strip() for it in itens)


# ---------------------------------------------------------------------------
# Persistência (padrão frota: _ler / _escrever_atomico)
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


def _proximo_id(itens):
    return (max((i.get("id", 0) for i in itens), default=0) + 1)


# ---------------------------------------------------------------------------
# Auditoria (etapas de tempo + log de ações)
# ---------------------------------------------------------------------------
def abrir_etapa(pregao_id, etapa, usuario, nota=None):
    a = _ler(AUDIT_PATH, AUDIT_VAZIO)
    a["etapas"].append({
        "id": _proximo_id(a["etapas"]), "pregao_id": pregao_id, "etapa": etapa,
        "usuario": usuario, "inicio": AGORA(), "fim": None, "nota": nota,
    })
    _escrever_atomico(a, AUDIT_PATH)


def fechar_etapa(pregao_id, etapa):
    a = _ler(AUDIT_PATH, AUDIT_VAZIO)
    for e in reversed(a["etapas"]):
        if e["pregao_id"] == pregao_id and e["etapa"] == etapa and e["fim"] is None:
            e["fim"] = AGORA()
            break
    _escrever_atomico(a, AUDIT_PATH)


def _limpar_devolucao(p):
    """Remove a marca de devolução do pregão (quando ele avança de novo após a
    correção). Chamado nas transições que fazem o pregão seguir em frente."""
    if p.get("devolucao"):
        p["devolucao"] = None


def log_acao(pregao_id, usuario, acao, detalhe=None):
    a = _ler(AUDIT_PATH, AUDIT_VAZIO)
    a["log"].append({
        "id": _proximo_id(a["log"]), "pregao_id": pregao_id, "usuario": usuario,
        "acao": acao, "detalhe": detalhe, "em": AGORA(),
    })
    _escrever_atomico(a, AUDIT_PATH)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
class NovoItem(BaseModel):
    numero_item: str
    descricao: str
    quantidade: Optional[int] = None
    teto_unitario: Optional[float] = None
    fornecemos: Optional[bool] = None    # Thiago marca no cadastro: fornecemos este item?
    especificacao_tecnica: Optional[str] = None   # descritivo técnico completo (do edital)
    modelo_referencia: Optional[str] = None        # marca/modelo de referência indicado no edital
    me_epp: Optional[str] = None         # "ampla" | "exclusivo" | "cota" (participação do item)
    grupo_id: Optional[int] = None       # a qual grupo o item pertence (None = avulso)

class GrupoIn(BaseModel):
    id: int                              # id local do grupo dentro do pregão
    nome: str                            # nome/número do grupo (ex.: "Grupo 1")
    teto_total: Optional[float] = None   # teto total do grupo (do edital)

class NovoPregao(BaseModel):
    orgao: str
    numero: str
    tipo: str
    tem_referencia: bool = False
    uasg: Optional[str] = None
    portal: Optional[str] = None
    objeto: Optional[str] = None
    criterio: Optional[str] = None
    modo_disputa: Optional[str] = None
    valor_estimado: Optional[float] = None
    valor_sigiloso: bool = False
    data_sessao: Optional[str] = None
    grupos: list[GrupoIn] = []
    itens: list[NovoItem] = []

class MudarStatus(BaseModel):
    status: str
    nota: Optional[str] = None


class ItemTriagem(BaseModel):
    """Um requisito de habilitação avaliado na triagem."""
    chave: str                       # ex.: "me_epp", "atestado", "econ_fin"
    rotulo: str                      # texto exibido
    situacao: str                    # "atende" | "nao_atende" | "na"
    obrigatorio: bool = True         # se não-atendido e obrigatório => impeditivo
    observacao: Optional[str] = None

class TriagemIn(BaseModel):
    """Resultado da triagem de habilitação de um pregão."""
    itens: list[ItemTriagem] = []
    decisao: str                     # "seguir" | "declinar"
    motivo_declinio: Optional[str] = None   # obrigatório quando decisao == "declinar"


class ItemValidacao(BaseModel):
    """Decisão do Adriano sobre um item do pregão."""
    id: int                          # id do item dentro do pregão
    validado: str                    # "aprovado" | "reprovado"
    motivo_reprova: Optional[str] = None    # obrigatório quando reprovado
    incluir: bool = False            # Adriano puxa de volta um item que o Thiago deixou de fora

class ValidacaoIn(BaseModel):
    """Conjunto de decisões do Adriano ao liberar o pregão para atribuição."""
    itens: list[ItemValidacao] = []


class SugestaoItem(BaseModel):
    """Uma sugestão de fabricante/modelo para um item."""
    fabricante: str
    modelo: str
    principal: bool = False

class ItemSugestoes(BaseModel):
    """As 3 sugestões definidas para um item."""
    id: int                          # id do item dentro do pregão
    sugestoes: list[SugestaoItem] = []

class SugestoesIn(BaseModel):
    """Conjunto de sugestões por item (etapa Definir 3 sugestões)."""
    itens: list[ItemSugestoes] = []


class CotacaoSugestao(BaseModel):
    """Preço cotado de uma sugestão (valor total com impostos, por unidade)."""
    id: int                              # id da sugestão dentro do item
    valor_total_impostos: Optional[float] = None
    distribuidor: Optional[str] = None   # de onde veio o preço (texto livre)

class CotacaoItem(BaseModel):
    """Cotação de um item: preços por sugestão, a escolhida e se está travada."""
    id: int                              # id do item dentro do pregão
    sugestoes: list[CotacaoSugestao] = []
    sugestao_escolhida_id: Optional[int] = None
    travada: bool = False

class CotacaoIn(BaseModel):
    """Estado da cotação (salvar rascunho / travar itens). Não avança o status."""
    itens: list[CotacaoItem] = []


class SugestaoEdit(BaseModel):
    """Edição de uma sugestão na cotação (coordenador): corrige fabricante/modelo."""
    id: int
    fabricante: Optional[str] = None
    modelo: Optional[str] = None

class EditarSugestoesIn(BaseModel):
    item_id: int
    sugestoes: list[SugestaoEdit] = []

class DeclinarItemIn(BaseModel):
    item_id: int
    motivo: str

class AddSugestaoIn(BaseModel):
    item_id: int
    fabricante: Optional[str] = None
    modelo: Optional[str] = None


class RevisaoItem(BaseModel):
    """Decisão da revisão para um item: aprovado ou declinado (com motivo)."""
    id: int                              # id do item dentro do pregão
    decisao: str                         # "aprovado" | "declinado"
    motivo: Optional[str] = None         # obrigatório quando declinado
    # Usados SÓ quando o item foi declinado na própria cotação e o
    # coordenador decide aprová-lo "mesmo assim" na revisão — pra participar
    # da disputa e acompanhar o que os concorrentes oferecem, mesmo sabendo
    # que não é competitivo. sugestao_id reaproveita uma sugestão já
    # cadastrada (com valor); se não houver, valor/fabricante/modelo definem
    # uma nova sugestão na hora (modelo cai pro modelo_referencia do edital
    # se não for informado).
    sugestao_id: Optional[int] = None
    valor: Optional[float] = None
    fabricante: Optional[str] = None
    modelo: Optional[str] = None

class RevisaoIn(BaseModel):
    """Revisão do Allan: decisão item a item. O destino do pregão é derivado:
    sobra >=1 aprovado -> pronto_effecti (só com aprovados); nenhum -> nao_realizado."""
    itens: list[RevisaoItem] = []
    motivo_geral: Optional[str] = None   # usado na Lição quando o pregão inteiro cai


# Requisitos da triagem — VARIAM conforme o tipo de pregão.
# Base comum a todos os tipos + acréscimos por tipo. O front recebe a lista
# já resolvida para o tipo do pregão (fonte única da verdade no back-end).
TRIAGEM_COMUM = [
    {"chave": "me_epp",       "rotulo": "Exclusivo / cota ME-EPP compatível com a empresa", "obrigatorio": True},
    {"chave": "econ_fin",     "rotulo": "Qualificação econômico-financeira (índices, capital/PL)", "obrigatorio": True},
    {"chave": "regularidade", "rotulo": "Regularidade fiscal e trabalhista em dia",       "obrigatorio": True},
    {"chave": "prazo",        "rotulo": "Prazo de entrega/execução é exequível",           "obrigatorio": False},
]
TRIAGEM_POR_TIPO = {
    "fornecimento": [
        {"chave": "atestado_forn", "rotulo": "Atestado de fornecimento de itens compatíveis atende ao exigido", "obrigatorio": True},
        {"chave": "marca_ref",     "rotulo": "Marcas/modelos de referência são atendíveis",  "obrigatorio": False},
        {"chave": "amostra",       "rotulo": "Não exige amostra/prova inviável no prazo",     "obrigatorio": False},
        {"chave": "entrega_local", "rotulo": "Local e condições de entrega são viáveis",      "obrigatorio": False},
    ],
    "locacao": [
        {"chave": "atestado_loca", "rotulo": "Atestado de locação de equipamentos semelhantes atende ao exigido", "obrigatorio": True},
        {"chave": "parque",        "rotulo": "Temos parque/equipamentos para atender a locação", "obrigatorio": True},
        {"chave": "assist_tec",    "rotulo": "Assistência técnica no prazo do contrato é viável", "obrigatorio": False},
        {"chave": "vistoria",      "rotulo": "Vistoria (se houver) é viável no prazo",         "obrigatorio": False},
    ],
    "servico": [
        {"chave": "atestado_serv", "rotulo": "Atestado de capacidade técnica em serviço/instalação de porte compatível", "obrigatorio": True},
        {"chave": "equipe_tec",    "rotulo": "Equipe técnica / responsável técnico exigido disponível", "obrigatorio": True},
        {"chave": "vistoria",      "rotulo": "Vistoria (se houver) é viável no prazo",         "obrigatorio": False},
        {"chave": "art",           "rotulo": "ART/registro de classe exigido é atendível",     "obrigatorio": False},
    ],
}

def _triagem_do_tipo(tipo: str) -> list:
    """Monta o checklist de habilitação conforme o tipo do pregão."""
    especificos = TRIAGEM_POR_TIPO.get(tipo, TRIAGEM_POR_TIPO["fornecimento"])
    # comum primeiro (obrigatórios de base), depois os específicos do tipo
    return TRIAGEM_COMUM + especificos


# ---------------------------------------------------------------------------
# Rotas — pregões
# ---------------------------------------------------------------------------
@router.get("/pregoes")
def listar_pregoes(arquivados: bool = False, user=Depends(usuario_logado)):
    """Resumo para o Kanban (sem itens, para ser leve). Por padrão NÃO traz os
    cards arquivados; com ?arquivados=true traz só eles (tela "Arquivados")."""
    pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
    pregoes = [p for p in pregoes if bool(p.get("arquivado")) == bool(arquivados)]
    return {"pregoes": [
        {"id": p["id"], "orgao": p["orgao"], "numero": p["numero"], "tipo": p["tipo"],
         "status": p["status"], "valor_estimado": p.get("valor_estimado"),
         "valor_sigiloso": p.get("valor_sigiloso", False),
         "data_sessao": p.get("data_sessao"), "responsavel": p.get("responsavel"),
         "total_itens": len(p.get("itens", [])),
         "resultado_motivo": p.get("resultado_motivo"),
         "n_anotacoes": len(p.get("anotacoes", [])),
         "n_documentos": len(p.get("documentos", [])),
         "analista_pode_cotar": analista_pode_cotar(p),
         "devolucao": p.get("devolucao"),
         "arquivado": bool(p.get("arquivado")),
         "arquivado_em": p.get("arquivado_em"), "arquivado_por": p.get("arquivado_por"),
         "arquivado_motivo": p.get("arquivado_motivo")}
        for p in pregoes
    ]}


@router.get("/pregoes/{pid}")
def detalhe_pregao(pid: int, user=Depends(usuario_logado)):
    for p in _ler(PREGOES_PATH, PREGOES_VAZIO):
        if p["id"] == pid:
            return p
    raise HTTPException(status_code=404, detail="Pregão não encontrado")


@router.get("/triagem-padrao")
def triagem_padrao(tipo: str = "fornecimento", user=Depends(usuario_logado)):
    """Checklist de habilitação conforme o TIPO do pregão (forn./locação/serviço).
    Alimenta a tela de triagem quando ainda não há triagem salva."""
    return {"tipo": tipo, "itens": _triagem_do_tipo(tipo)}


# ---------------------------------------------------------------------------
# Ajustes do coordenador (Ponto 4): editar campos (cabeçalho + itens) e
# retroceder o pregão uma etapa. Restrito ao coordenador. Fica no menu ⋯ do card.
# ---------------------------------------------------------------------------
def _exige_coordenador(user):
    if user.get("papel") != "coordenador":
        raise HTTPException(status_code=403, detail="Apenas o coordenador pode ajustar o pregão.")

class EditarItem(BaseModel):
    id: int
    descricao: Optional[str] = None
    especificacao_tecnica: Optional[str] = None
    quantidade: Optional[int] = None
    teto_unitario: Optional[float] = None
    me_epp: Optional[str] = None
    modelo_referencia: Optional[str] = None
    fornecemos: Optional[bool] = None

class EditarPregao(BaseModel):
    orgao: Optional[str] = None
    numero: Optional[str] = None
    uasg: Optional[str] = None
    objeto: Optional[str] = None
    valor_estimado: Optional[float] = None
    valor_sigiloso: Optional[bool] = None
    data_sessao: Optional[str] = None
    tem_referencia: Optional[bool] = None
    itens: Optional[list[EditarItem]] = None

@router.patch("/pregoes/{pid}")
def editar_pregao(pid: int, dados: EditarPregao, user=Depends(usuario_logado)):
    """Corrige campos do pregão (cabeçalho e/ou itens). Coordenador sempre; o
    analista pode editar quando o pregão está na etapa dele (triagem) — ex.:
    preencher o modelo de referência que faltou.
    A quantidade continua exigindo > 0 (não pode zerar por engano)."""
    quem = user["nome"]
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        papel = user.get("papel")
        if papel != "coordenador":
            if not (papel in ("analista", "analista_cotacao") and p.get("status") == "triagem"):
                raise HTTPException(status_code=403,
                    detail="Edição restrita ao coordenador (analista só na triagem).")
        # cabeçalho
        for campo in ("orgao", "numero", "uasg", "objeto", "data_sessao"):
            val = getattr(dados, campo)
            if val is not None:
                p[campo] = val.strip() if isinstance(val, str) else val
        # valor estimado / sigiloso — reforçado aqui (não confia só no front):
        # sigiloso SEMPRE zera o valor, mesmo que valor_estimado tenha vindo
        # preenchido por engano na mesma requisição.
        if dados.valor_sigiloso is not None:
            p["valor_sigiloso"] = dados.valor_sigiloso
        if p.get("valor_sigiloso"):
            p["valor_estimado"] = None
        elif dados.valor_estimado is not None:
            if dados.valor_estimado < 0:
                raise HTTPException(status_code=400, detail="Valor estimado inválido.")
            p["valor_estimado"] = dados.valor_estimado
        # "tem modelo de referência?" — se mudar e o pregão ainda não passou
        # pela atribuição do Rodrigo (ainda em triagem/validação/atribuição),
        # recalcula a rota automática (responsável + destino) na hora, pra
        # não ficar com uma atribuição desatualizada esperando confirmação.
        reroteado = False
        novo_responsavel = None
        if dados.tem_referencia is not None and dados.tem_referencia != p.get("tem_referencia"):
            p["tem_referencia"] = dados.tem_referencia
            if p.get("status") in ("triagem", "aguardando_adriano", "aguardando_rodrigo"):
                rota = rotear_atribuicao(p)
                if p.get("status") == "aguardando_rodrigo":
                    p["atribuicao"] = {
                        "responsavel": rota["responsavel"], "apoio": rota["apoio"],
                        "proximo_status": rota["proximo_status"], "motivo": rota["motivo"],
                        "automatica": True, "por": quem, "em": AGORA(),
                    }
                    p["responsavel"] = rota["responsavel"]
                    reroteado = True
                    novo_responsavel = rota["responsavel"]
        # itens
        if dados.itens:
            imap = {it["id"]: it for it in p.get("itens", [])}
            for ed in dados.itens:
                it = imap.get(ed.id)
                if not it:
                    continue
                if ed.descricao is not None:
                    it["descricao"] = ed.descricao.strip()
                if ed.especificacao_tecnica is not None:
                    it["especificacao_tecnica"] = ed.especificacao_tecnica.strip()
                if ed.quantidade is not None:
                    if ed.quantidade <= 0:
                        raise HTTPException(status_code=400,
                            detail=f"Item {it.get('numero_item')}: a quantidade deve ser maior que zero.")
                    it["quantidade"] = ed.quantidade
                if ed.teto_unitario is not None:
                    if ed.teto_unitario < 0:
                        raise HTTPException(status_code=400,
                            detail=f"Item {it.get('numero_item')}: teto inválido.")
                    it["teto_unitario"] = ed.teto_unitario
                if ed.me_epp is not None and ed.me_epp in ("ampla", "exclusivo", "cota"):
                    it["me_epp"] = ed.me_epp
                if ed.modelo_referencia is not None:
                    it["modelo_referencia"] = ed.modelo_referencia.strip() or None
                if ed.fornecemos is not None:
                    it["fornecemos"] = ed.fornecemos
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    log_acao(pid, quem, "editou_pregao", {"reroteado": reroteado, "novo_responsavel": novo_responsavel} if reroteado else {})
    return {"ok": True, "reroteado": reroteado, "novo_responsavel": novo_responsavel}

class ReatribuirIn(BaseModel):
    responsavel: str

@router.post("/pregoes/{pid}/reatribuir")
def reatribuir_responsavel(pid: int, dados: ReatribuirIn, user=Depends(usuario_logado)):
    """Troca só o responsável do pregão, sem mudar de etapa e sem tocar em
    sugestões, cotação, datasheets ou qualquer outro dado já lançado. Uso:
    reequilibrar carga de trabalho (ex.: Rafael/Pedro sem tempo -> Jorge
    assume a cotação sem perder o que já foi preenchido). Só coordenador;
    só quando o pregão já tem responsável (pós-atribuição) e não está
    encerrado."""
    quem = user["nome"]
    _exige_coordenador(user)
    novo = (dados.responsavel or "").strip()
    if not novo:
        raise HTTPException(status_code=400, detail="Informe o novo responsável.")
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        if p.get("status") in ("ganho", "perdido", "nao_realizado"):
            raise HTTPException(status_code=409, detail="Este pregão já está encerrado.")
        anterior = p.get("responsavel")
        if not anterior:
            raise HTTPException(status_code=409,
                detail="Este pregão ainda não tem responsável (aguarda atribuição).")
        if novo == anterior:
            raise HTTPException(status_code=400, detail="Este já é o responsável atual.")
        papeis_ok = {"analista", "analista_cotacao", "operacional", "coordenador"}
        ativos = {u["nome"] for u in auth_store.listar_publico() if u.get("papel") in papeis_ok}
        if novo not in ativos:
            raise HTTPException(status_code=400,
                detail="Responsável inválido: selecione um usuário ativo da lista.")
        p["responsavel"] = novo
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    log_acao(pid, quem, "reatribuiu_responsavel", {"de": anterior, "para": novo})
    return {"ok": True, "responsavel": novo}


@router.post("/pregoes/{pid}/retroceder")
def retroceder_etapa(pid: int, user=Depends(usuario_logado)):
    """Volta o pregão uma etapa no fluxo principal. Só coordenador."""
    quem = user["nome"]
    _exige_coordenador(user)
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        atual = p["status"]
        if atual not in SEQ_STATUS:
            raise HTTPException(status_code=409,
                detail="Este pregão não está numa etapa que possa retroceder.")
        i = SEQ_STATUS.index(atual)
        if i == 0:
            raise HTTPException(status_code=409, detail="O pregão já está na primeira etapa.")
        anterior = SEQ_STATUS[i - 1]
        p["status"] = anterior
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    # fecha a etapa atual e reabre a anterior na trilha de tempo
    if ETAPA_DE.get(atual):
        fechar_etapa(pid, ETAPA_DE[atual])
    if ETAPA_DE.get(anterior):
        abrir_etapa(pid, ETAPA_DE[anterior], quem)
    log_acao(pid, quem, "retrocedeu", {"de": atual, "para": anterior})
    return {"ok": True, "status": anterior}


class DevolverIn(BaseModel):
    motivo: str

@router.post("/pregoes/{pid}/devolver")
def devolver_etapa(pid: int, dados: DevolverIn, background_tasks: BackgroundTasks, user=Depends(usuario_logado)):
    """Reprovar avanço: o responsável da etapa atual devolve o pregão à etapa
    anterior, com motivo, para correção. Diferente do 'retroceder' (só
    coordenador, sem motivo), aqui quem está na etapa pode devolver. O motivo
    fica como ocorrência no pregão e é registrado na auditoria."""
    quem = user["nome"]
    if not (dados.motivo or "").strip():
        raise HTTPException(status_code=400, detail="Informe o motivo da devolução.")
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        atual = p["status"]
        if atual not in SEQ_STATUS:
            raise HTTPException(status_code=409, detail="Este pregão não está numa etapa que possa devolver.")
        i = SEQ_STATUS.index(atual)
        if i == 0:
            raise HTTPException(status_code=409, detail="O pregão já está na primeira etapa.")
        # quem pode devolver: coordenador sempre, ou o responsável da etapa atual
        etapa_atual = ETAPA_DE.get(atual)
        if user.get("papel") != "coordenador":
            exige_papel(user, etapa_atual)   # 403 se não for dono da etapa
        anterior = SEQ_STATUS[i - 1]
        p["status"] = anterior
        # registra a devolução como ocorrência (visível a todos)
        anot = p.setdefault("anotacoes", [])
        anot.append({
            "id": _proximo_id(anot), "tipo": "geral",
            "texto": f"Avanço reprovado por {quem} (de {atual} para {anterior}): {dados.motivo.strip()}",
            "por": quem, "em": AGORA(),
        })
        # marca a devolução pendente no card (some quando o pregão avançar de novo).
        # 'para_status' é a etapa que precisa corrigir (a anterior, agora atual).
        p["devolucao"] = {
            "motivo": dados.motivo.strip(), "por": quem,
            "de_status": atual, "para_status": anterior, "em": AGORA(),
        }
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    if ETAPA_DE.get(atual):
        fechar_etapa(pid, ETAPA_DE[atual])
    if ETAPA_DE.get(anterior):
        abrir_etapa(pid, ETAPA_DE[anterior], quem)
    log_acao(pid, quem, "devolveu", {"de": atual, "para": anterior, "motivo": dados.motivo.strip()})

    # e-mail: quem precisa corrigir. O campo 'responsavel' do pregão só é
    # confiável pra isso quando o destino é 'em_cotacao' (mesma pessoa que
    # já estava cotando) — nos outros destinos (triagem, validação,
    # atribuição, definir sugestões, revisão, entrega) ele ainda aponta pra
    # quem cotou, não pra quem precisa agir agora; nesses casos avisa por
    # papel da etapa de destino (ninguém tem "dono" individual ali).
    destinatarios = _emails_do_responsavel(p) if anterior == "em_cotacao" else []
    if not destinatarios:
        destinatarios = _emails_por_papel(PAPEL_DA_ETAPA.get(ETAPA_DE.get(anterior), set()))
    if destinatarios:
        motivo_html = dados.motivo.strip().replace("<", "&lt;")
        background_tasks.add_task(
            enviar_email,
            destinatarios=destinatarios,
            assunto=f"⤺ Pregão devolvido — {p['orgao']} PE {p['numero']}",
            titulo="Um pregão foi devolvido para correção",
            corpo_html=(
                f"<p><b>{p['orgao']}</b><br>PE {p['numero']}</p>"
                f"<p>{quem} devolveu o pregão de <b>{STATUS_ROTULO.get(atual, atual)}</b> "
                f"para <b>{STATUS_ROTULO.get(anterior, anterior)}</b>, com o motivo:</p>"
                f"<p style=\"background:#fdf2f2;border-left:3px solid #b23b3b;padding:10px 14px;"
                f"border-radius:6px;margin:10px 0\">{motivo_html}</p>"
                f"<p>Corrija o que for preciso para o pregão seguir adiante.</p>"
            ),
            evento="devolucao",
            cta_texto="Abrir o Gestor de Pregões",
            cta_url=INTRANET_BASE_URL,
        )
    return {"ok": True, "status": anterior, "para": anterior}


class ExcluirPregaoIn(BaseModel):
    confirmar_numero: str    # a pessoa digita o número do pregão para confirmar

@router.delete("/pregoes/{pid}")
def excluir_pregao(pid: int, dados: ExcluirPregaoIn, user=Depends(usuario_logado)):
    """Exclui DEFINITIVAMENTE um pregão que caiu por engano em 'nao_realizado'
    ou que já está ARQUIVADO (ex.: erro de cadastro). Só coordenador. Apaga o pregão, os
    arquivos em disco (documentos e datasheets), e os registros ligados
    (auditoria, lições, histórico). Irreversível. Exige digitar o número do
    pregão para confirmar."""
    quem = user["nome"]
    _exige_coordenador(user)
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        if p.get("status") != "nao_realizado" and not p.get("arquivado"):
            raise HTTPException(status_code=409,
                detail="Só é possível excluir de vez um pregão em 'Não realizado / não participado' "
                       "ou que já esteja arquivado. Arquive o card primeiro.")
        # confirmação: o número digitado precisa bater com o do pregão
        if (dados.confirmar_numero or "").strip() != (p.get("numero") or "").strip():
            raise HTTPException(status_code=400,
                detail="Confirmação incorreta: digite exatamente o número do pregão.")

        # apaga arquivos em disco (documentos e datasheets), tolerando ausências
        for d in p.get("documentos", []):
            try:
                cam = BASE / d.get("arquivo_path", "")
                if d.get("arquivo_path") and cam.exists():
                    cam.unlink()
            except Exception:
                pass
        for it in p.get("itens", []):
            try:
                dp = it.get("datasheet_path")
                if dp and (BASE / dp).exists():
                    (BASE / dp).unlink()
            except Exception:
                pass

        # remove o pregão do JSON
        pregoes = [x for x in pregoes if x["id"] != pid]
        _escrever_atomico(pregoes, PREGOES_PATH)

        # remove registros ligados: lições e histórico
        try:
            licoes = _ler(LICOES_PATH, LICOES_VAZIO)
            licoes = [l for l in licoes if l.get("pregao_id") != pid]
            _escrever_atomico(licoes, LICOES_PATH)
        except Exception:
            pass
        try:
            hist = _ler(HISTORICO_PATH, HISTORICO_VAZIO)
            if isinstance(hist, list):
                hist = [h for h in hist if h.get("pregao_id") != pid]
                _escrever_atomico(hist, HISTORICO_PATH)
        except Exception:
            pass
        # auditoria: remove etapas e logs do pregão
        try:
            a = _ler(AUDIT_PATH, AUDIT_VAZIO)
            a["etapas"] = [e for e in a.get("etapas", []) if e.get("pregao_id") != pid]
            a["log"] = [g for g in a.get("log", []) if g.get("pregao_id") != pid]
            _escrever_atomico(a, AUDIT_PATH)
        except Exception:
            pass
    return {"ok": True, "excluido": pid}


# ---------------------------------------------------------------------------
# Arquivar / restaurar card (coordenador). O card sai do Kanban e das rotinas
# (SLA), mas itens, documentos, anotações e histórico ficam guardados e dá para
# restaurar. Não vale para pregões que já tiveram disputa (em_disputa, ganho,
# perdido). A exclusão definitiva fica na tela "Arquivados" (DELETE /pregoes/{id}).
# ---------------------------------------------------------------------------
STATUS_NAO_ARQUIVAVEIS = {"em_disputa", "ganho", "perdido"}

class ArquivarIn(BaseModel):
    motivo: str

@router.post("/pregoes/{pid}/arquivar")
def arquivar_pregao(pid: int, dados: ArquivarIn, user=Depends(usuario_logado)):
    quem = user["nome"]
    _exige_coordenador(user)
    motivo = (dados.motivo or "").strip()
    if len(motivo) < 3:
        raise HTTPException(status_code=400, detail="Informe o motivo do arquivamento.")
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        if p.get("arquivado"):
            raise HTTPException(status_code=409, detail="Este pregão já está arquivado.")
        if p.get("status") in STATUS_NAO_ARQUIVAVEIS:
            raise HTTPException(status_code=409,
                detail="Pregões em disputa, ganhos ou perdidos não podem ser arquivados.")
        p["arquivado"] = True
        p["arquivado_em"] = AGORA()
        p["arquivado_por"] = quem
        p["arquivado_motivo"] = motivo
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    log_acao(pid, quem, "arquivou", {"motivo": motivo, "status": p.get("status")})
    return {"ok": True, "arquivado": True}


@router.post("/pregoes/{pid}/restaurar")
def restaurar_pregao(pid: int, user=Depends(usuario_logado)):
    quem = user["nome"]
    _exige_coordenador(user)
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        if not p.get("arquivado"):
            raise HTTPException(status_code=409, detail="Este pregão não está arquivado.")
        for k in ("arquivado", "arquivado_em", "arquivado_por", "arquivado_motivo"):
            p.pop(k, None)
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    log_acao(pid, quem, "restaurou", {"status": p.get("status")})
    return {"ok": True, "status": p.get("status")}


# ---------------------------------------------------------------------------
# Anotações / ocorrências do pregão (Thiago/Luana registram; todos veem).
# Tipos: adiamento, esclarecimento, geral. No adiamento, pode atualizar a
# data da sessão do pregão. Guardadas em p["anotacoes"] (histórico).
# ---------------------------------------------------------------------------
TIPOS_ANOTACAO = {"adiamento", "esclarecimento", "geral"}

class AnotacaoIn(BaseModel):
    tipo: str = "geral"
    texto: str
    nova_data_sessao: Optional[str] = None   # só faz sentido em 'adiamento'

def _pode_anotar(user):
    # analista (Thiago/Luana) e coordenador
    exige_papel(user, "triagem")

@router.get("/pregoes/{pid}/anotacoes")
def listar_anotacoes(pid: int, user=Depends(usuario_logado)):
    p = next((x for x in _ler(PREGOES_PATH, PREGOES_VAZIO) if x["id"] == pid), None)
    if not p:
        raise HTTPException(status_code=404, detail="Pregão não encontrado")
    anot = sorted(p.get("anotacoes", []),
                  key=lambda a: (a.get("em") or "", a.get("id") or 0), reverse=True)
    return {"anotacoes": anot, "data_sessao": p.get("data_sessao")}

@router.post("/pregoes/{pid}/anotacoes")
def criar_anotacao(pid: int, dados: AnotacaoIn, background_tasks: BackgroundTasks, user=Depends(usuario_logado)):
    quem = user["nome"]
    _pode_anotar(user)
    if dados.tipo not in TIPOS_ANOTACAO:
        raise HTTPException(status_code=400, detail="Tipo de anotação inválido.")
    if not (dados.texto or "").strip():
        raise HTTPException(status_code=400, detail="A anotação não pode ser vazia.")
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        anot = p.setdefault("anotacoes", [])
        nova = {
            "id": _proximo_id(anot), "tipo": dados.tipo, "texto": dados.texto.strip(),
            "por": quem, "em": AGORA(), "editado_em": None,
        }
        anot.append(nova)
        # adiamento pode reprogramar a data da sessão
        if dados.tipo == "adiamento" and (dados.nova_data_sessao or "").strip():
            p["data_sessao"] = dados.nova_data_sessao.strip()
            nova["nova_data_sessao"] = p["data_sessao"]
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    log_acao(pid, quem, "anotacao", {"tipo": dados.tipo})

    # esclarecimento e adiamento avisam por e-mail (geral não — só fica no
    # histórico do card). Vai pra todos os coordenadores + quem está com o
    # pregão agora, já que qualquer um deles pode precisar reagir.
    if dados.tipo in ("esclarecimento", "adiamento"):
        destinatarios = sorted(set(_emails_coordenadores()) | set(_emails_do_responsavel(p)))
        if destinatarios:
            rotulo_tipo = "Esclarecimento" if dados.tipo == "esclarecimento" else "Adiamento de sessão"
            texto_html = dados.texto.strip().replace("<", "&lt;")
            data_html = (f"<p><b>Nova data da sessão:</b> {nova['nova_data_sessao']}</p>"
                         if nova.get("nova_data_sessao") else "")
            background_tasks.add_task(
                enviar_email,
                destinatarios=destinatarios,
                assunto=f"📌 {rotulo_tipo} — {p['orgao']} PE {p['numero']}",
                titulo=f"Nova anotação: {rotulo_tipo.lower()}",
                corpo_html=(
                    f"<p><b>{p['orgao']}</b><br>PE {p['numero']}</p>"
                    f"<p>{quem} registrou:</p>"
                    f"<p style=\"background:#f6f8f7;border-left:3px solid #7AC143;padding:10px 14px;"
                    f"border-radius:6px;margin:10px 0\">{texto_html}</p>"
                    + data_html
                ),
                evento=f"anotacao_{dados.tipo}",
                cta_texto="Abrir o Gestor de Pregões",
                cta_url=INTRANET_BASE_URL,
            )
    return {"ok": True, "anotacao": nova, "data_sessao": p.get("data_sessao")}

@router.patch("/pregoes/{pid}/anotacoes/{aid}")
def editar_anotacao(pid: int, aid: int, dados: AnotacaoIn, user=Depends(usuario_logado)):
    quem = user["nome"]
    _pode_anotar(user)
    if not (dados.texto or "").strip():
        raise HTTPException(status_code=400, detail="A anotação não pode ser vazia.")
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        a = next((x for x in p.get("anotacoes", []) if x["id"] == aid), None)
        if not a:
            raise HTTPException(status_code=404, detail="Anotação não encontrada")
        if dados.tipo in TIPOS_ANOTACAO:
            a["tipo"] = dados.tipo
        a["texto"] = dados.texto.strip()
        a["editado_em"] = AGORA()
        if a["tipo"] == "adiamento" and (dados.nova_data_sessao or "").strip():
            p["data_sessao"] = dados.nova_data_sessao.strip()
            a["nova_data_sessao"] = p["data_sessao"]
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    log_acao(pid, quem, "anotacao_editada", {"id": aid})
    return {"ok": True, "anotacao": a, "data_sessao": p.get("data_sessao")}


# ---------------------------------------------------------------------------
# Documentos do pregão (edital, termo de referência, anexos). Ficam no card do
# início ao fim. Todos os logados BAIXAM; só analista (Thiago/Luana) e o
# coordenador ANEXAM/REMOVEM. Somente PDF, até 50 MB.
# ---------------------------------------------------------------------------
def _pode_docs(user):
    exige_papel(user, "triagem")   # analista (Thiago/Luana) + coordenador

@router.get("/pregoes/{pid}/documentos")
def listar_documentos(pid: int, user=Depends(usuario_logado)):
    """Lista os documentos do pregão (qualquer logado consulta)."""
    p = next((x for x in _ler(PREGOES_PATH, PREGOES_VAZIO) if x["id"] == pid), None)
    if not p:
        raise HTTPException(status_code=404, detail="Pregão não encontrado")
    docs = sorted(p.get("documentos", []), key=lambda d: (d.get("em") or "", d.get("id") or 0))
    # não expõe o caminho em disco; só metadados
    return {"documentos": [{
        "id": d["id"], "nome": d.get("nome"), "tamanho": d.get("tamanho"),
        "por": d.get("por"), "em": d.get("em"),
    } for d in docs]}

@router.post("/pregoes/{pid}/documentos")
async def upload_documento(pid: int, arquivo: UploadFile = File(...),
                           user=Depends(usuario_logado)):
    """Anexa um PDF ao pregão (analista/coordenador). Até 50 MB."""
    quem = user["nome"]
    _pode_docs(user)
    nome = (arquivo.filename or "").lower()
    if not nome.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Envie um arquivo PDF.")
    conteudo = await arquivo.read()
    if len(conteudo) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo muito grande (máx. 50 MB).")

    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        docs = p.setdefault("documentos", [])
        doc_id = _proximo_id(docs)

        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        seguro = "".join(c if c.isalnum() or c in "._-" else "_" for c in (arquivo.filename or "documento.pdf"))
        destino = DOCS_DIR / f"{pid}_{doc_id}_{seguro}"
        fd, tmp = tempfile.mkstemp(dir=str(DOCS_DIR), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(conteudo); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, destino)
        except Exception as e:
            if os.path.exists(tmp): os.remove(tmp)
            raise HTTPException(status_code=500, detail=f"Falha ao salvar documento: {e}")

        doc = {
            "id": doc_id, "nome": arquivo.filename or "documento.pdf",
            "arquivo_path": str(destino.relative_to(BASE)),
            "tamanho": len(conteudo), "por": quem, "em": AGORA(),
        }
        docs.append(doc)
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

    log_acao(pid, quem, "anexou_documento", {"doc": doc_id, "arquivo": arquivo.filename})
    return {"ok": True, "documento": {"id": doc["id"], "nome": doc["nome"],
            "tamanho": doc["tamanho"], "por": doc["por"], "em": doc["em"]}}

@router.get("/pregoes/{pid}/documentos/{doc_id}/download")
def baixar_documento(pid: int, doc_id: int, user=Depends(usuario_logado)):
    """Baixa um documento do pregão (qualquer logado)."""
    p = next((x for x in _ler(PREGOES_PATH, PREGOES_VAZIO) if x["id"] == pid), None)
    if not p:
        raise HTTPException(status_code=404, detail="Pregão não encontrado")
    d = next((x for x in p.get("documentos", []) if x["id"] == doc_id), None)
    if not d:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    caminho = BASE / d["arquivo_path"]
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="Arquivo não está mais no servidor.")
    return FileResponse(str(caminho), media_type="application/pdf", filename=d.get("nome") or "documento.pdf")

@router.delete("/pregoes/{pid}/documentos/{doc_id}")
def remover_documento(pid: int, doc_id: int, user=Depends(usuario_logado)):
    """Remove um documento do pregão (analista/coordenador)."""
    quem = user["nome"]
    _pode_docs(user)
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        d = next((x for x in p.get("documentos", []) if x["id"] == doc_id), None)
        if not d:
            raise HTTPException(status_code=404, detail="Documento não encontrado")
        # apaga o arquivo em disco (se existir) e o registro
        try:
            caminho = BASE / d["arquivo_path"]
            if caminho.exists():
                caminho.unlink()
        except Exception:
            pass
        p["documentos"] = [x for x in p.get("documentos", []) if x["id"] != doc_id]
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    log_acao(pid, quem, "removeu_documento", {"doc": doc_id})
    return {"ok": True}



    """Checklist de habilitação conforme o TIPO do pregão (forn./locação/serviço)."""
    return {"tipo": tipo, "itens": _triagem_do_tipo(tipo)}


@router.post("/pregoes/{pid}/triagem")
def salvar_triagem(pid: int, dados: TriagemIn, user=Depends(usuario_logado)):
    """
    Registra a triagem de habilitação e move o pregão:
      - decisao == "seguir"   -> status "aguardando_adriano"
      - decisao == "declinar" -> status "nao_realizado" (+ Lição Aprendida)
    Guarda o resultado no próprio pregão (campo "triagem") para consulta.
    """
    quem = user["nome"]
    exige_papel(user, "triagem")
    if dados.decisao not in ("seguir", "declinar"):
        raise HTTPException(status_code=400, detail="Decisão inválida")

    # trava de segurança: não deixa 'seguir' com requisito obrigatório não atendido
    impeditivos = [i for i in dados.itens if i.obrigatorio and i.situacao == "nao_atende"]
    if dados.decisao == "seguir" and impeditivos:
        raise HTTPException(
            status_code=400,
            detail="Há requisito obrigatório não atendido; decline ou ajuste a avaliação.",
        )
    if dados.decisao == "declinar" and not (dados.motivo_declinio or "").strip():
        raise HTTPException(status_code=400, detail="Informe o motivo do declínio.")

    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        antigo = p["status"]
        p["triagem"] = {
            "itens": [i.model_dump() for i in dados.itens],
            "decisao": dados.decisao,
            "motivo_declinio": dados.motivo_declinio,
            "por": quem, "em": AGORA(),
        }
        novo = "aguardando_adriano" if dados.decisao == "seguir" else "nao_realizado"
        p["status"] = novo
        if dados.decisao == "seguir":
            _limpar_devolucao(p)
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

        if dados.decisao == "declinar":
            licoes = _ler(LICOES_PATH, LICOES_VAZIO)
            licoes.append({
                "id": _proximo_id(licoes), "pregao_id": pid,
                "orgao": p.get("orgao"), "numero": p.get("numero"),
                "origem": "triagem", "motivo": dados.motivo_declinio,
                "por": quem, "em": AGORA(),
            })
            _escrever_atomico(licoes, LICOES_PATH)

    # auditoria: fecha triagem, abre a etapa seguinte, registra o log
    fechar_etapa(pid, "triagem")
    if dados.decisao == "seguir":
        abrir_etapa(pid, "validacao", quem)
    log_acao(pid, quem, "triagem", {"decisao": dados.decisao,
                                     "motivo": dados.motivo_declinio})
    return {"ok": True, "status": novo}


@router.post("/pregoes/{pid}/validacao")
def salvar_validacao(pid: int, dados: ValidacaoIn, user=Depends(usuario_logado)):
    """
    Validação técnica do Adriano. Regras:
      - Todo item marcado 'fornecemos' precisa ser decidido (aprovado, ou
        reprovado COM motivo). Reprovado com motivo conta como decidido.
      - O Adriano pode 'incluir' itens que o Thiago deixou de fora.
      - Ao liberar, o pregão vai para 'aguardando_rodrigo' (atribuição).
    """
    quem = user["nome"]
    exige_papel(user, "validacao")
    decisoes = {d.id: d for d in dados.itens}

    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")

        # aplica as decisões recebidas sobre os itens
        for it in p["itens"]:
            d = decisoes.get(it["id"])
            if not d:
                continue
            # inclusão de item que estava fora
            if d.incluir and not it.get("fornecemos"):
                it["fornecemos"] = True
                it["incluido_adriano"] = True
            if d.validado == "aprovado":
                it["validado"] = "aprovado"; it["motivo_reprova"] = None
            elif d.validado == "reprovado":
                if not (d.motivo_reprova or "").strip():
                    raise HTTPException(status_code=400,
                        detail=f"Item {it['numero_item']} reprovado exige motivo.")
                it["validado"] = "reprovado"; it["motivo_reprova"] = d.motivo_reprova

        # trava: todo item que fornecemos precisa estar decidido
        pendentes = [it for it in p["itens"]
                     if it.get("fornecemos") and it["validado"] == "pendente"]
        if pendentes:
            nums = ", ".join(it["numero_item"] for it in pendentes)
            raise HTTPException(status_code=400,
                detail=f"Itens ainda pendentes de validação: {nums}")

        # se NENHUM item ficou aprovado, não há o que cotar: vira não realizado
        aprovados_ct = [it for it in p["itens"] if it.get("validado") == "aprovado"]
        if not aprovados_ct:
            p["status"] = "nao_realizado"
            p["resultado"] = "nao_participado"
            p["resultado_motivo"] = "Todos os itens reprovados na validação."
            p["resultado_em"] = AGORA(); p["resultado_por"] = quem
            p["atualizado_em"] = AGORA()
            # lição
            licoes = _ler(LICOES_PATH, LICOES_VAZIO)
            licoes.append({
                "id": _proximo_id(licoes), "pregao_id": pid,
                "orgao": p.get("orgao"), "numero": p.get("numero"),
                "origem": "validacao", "numero_item": None,
                "descricao": "Todos os itens reprovados", "motivo": "Todos os itens reprovados na validação.",
                "por": quem, "em": AGORA(),
            })
            _escrever_atomico(licoes, LICOES_PATH)
            _escrever_atomico(pregoes, PREGOES_PATH)
            fechar_etapa(pid, "validacao")
            log_acao(pid, quem, "validacao_adriano", {"aprovados": 0, "nao_realizado": True})
            return {"ok": True, "status": "nao_realizado", "aprovados": 0,
                    "reprovados": sum(1 for it in p["itens"] if it["validado"] == "reprovado"),
                    "nao_realizado": True}

        p["status"] = "aguardando_rodrigo"
        _limpar_devolucao(p)
        # a regra já decide a atribuição; o Rodrigo só ajusta exceção
        rota = rotear_atribuicao(p)
        p["atribuicao"] = {
            "responsavel": rota["responsavel"], "apoio": rota["apoio"],
            "proximo_status": rota["proximo_status"], "motivo": rota["motivo"],
            "automatica": True, "por": quem, "em": AGORA(),
        }
        p["responsavel"] = rota["responsavel"]
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

    fechar_etapa(pid, "validacao")
    abrir_etapa(pid, "atribuicao", quem)
    aprovados = sum(1 for it in p["itens"] if it["validado"] == "aprovado")
    reprovados = sum(1 for it in p["itens"] if it["validado"] == "reprovado")
    incluidos = sum(1 for it in p["itens"] if it.get("incluido_adriano"))
    log_acao(pid, quem, "validacao_adriano",
             {"aprovados": aprovados, "reprovados": reprovados, "incluidos": incluidos})
    return {"ok": True, "status": "aguardando_rodrigo",
            "aprovados": aprovados, "reprovados": reprovados, "incluidos": incluidos}


class AtribuicaoIn(BaseModel):
    """Ajuste do Rodrigo. Se 'manter' = True, confirma a atribuição automática."""
    manter: bool = True
    responsavel: Optional[str] = None       # usado quando manter = False
    proximo_status: Optional[str] = None    # opcional: força o destino
    nota: Optional[str] = None


@router.get("/pregoes/{pid}/responsaveis")
def responsaveis_atribuicao(pid: int, user=Depends(usuario_logado)):
    """Lista de opções de responsável para o ajuste manual da atribuição.
    Vem dos usuários reais ativos: analista + operacional + coordenador. Se o
    pregão for elegível (fornecimento + todos itens com referência), os
    analistas entram como quem pode cotar; caso contrário, também aparecem para
    outras etapas, mas a permissão de cotar é validada na cotação."""
    p = next((x for x in _ler(PREGOES_PATH, PREGOES_VAZIO) if x["id"] == pid), None)
    if not p:
        raise HTTPException(status_code=404, detail="Pregão não encontrado")
    papeis_ok = {"analista", "analista_cotacao", "operacional", "coordenador"}
    nomes = []
    for u in auth_store.listar_publico():
        if u.get("papel") in papeis_ok:
            nomes.append({"nome": u["nome"], "papel": u["papel"]})
    return {"responsaveis": nomes, "analista_pode_cotar": analista_pode_cotar(p)}


@router.post("/pregoes/{pid}/atribuicao")
def confirmar_atribuicao(pid: int, dados: AtribuicaoIn, background_tasks: BackgroundTasks, user=Depends(usuario_logado)):
    """
    Conclui a etapa de atribuição e envia o pregão ao destino:
      - manter=True  -> usa a atribuição automática (regra por tipo+referência)
      - manter=False -> aplica o responsável informado pelo Rodrigo (exceção)
    Destino: 'em_cotacao' (Rafael/Pedro ou Osvair) ou 'definir_sugestoes' (Allan).
    """
    quem = user["nome"]
    exige_papel(user, "atribuicao")
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        if p["status"] != "aguardando_rodrigo":
            raise HTTPException(status_code=400, detail="Pregão não está em atribuição.")

        rota = rotear_atribuicao(p)   # recomputa a regra (fonte da verdade)
        if dados.manter:
            responsavel = rota["responsavel"]
            proximo = rota["proximo_status"]
            ajuste = False
        else:
            responsavel = (dados.responsavel or "").strip() or rota["responsavel"]
            proximo = dados.proximo_status or rota["proximo_status"]
            ajuste = True

        p["atribuicao"] = {
            "responsavel": responsavel, "apoio": rota["apoio"],
            "proximo_status": proximo, "motivo": rota["motivo"],
            "automatica": not ajuste, "por": quem, "em": AGORA(),
            "nota": dados.nota,
        }
        p["responsavel"] = responsavel
        p["status"] = proximo
        _limpar_devolucao(p)
        # Se vai direto para cotação (sem passar por 'definir sugestões'),
        # materializa o modelo de referência do edital como 1ª sugestão, para a
        # cotação não aparecer vazia. Só para itens que fornecemos, não
        # reprovados e ainda sem sugestões.
        if proximo == "em_cotacao":
            for it in p["itens"]:
                if not it.get("fornecemos") or it.get("validado") == "reprovado":
                    continue
                if it.get("sugestoes"):
                    continue
                ref = (it.get("modelo_referencia") or "").strip()
                if ref:
                    # o modelo de referência do edital vira a sugestão principal;
                    # fabricante fica em branco para o operador completar na cotação
                    it["sugestoes"] = [{"id": 1, "fabricante": "", "modelo": ref, "principal": True}]
                    it["sugestao_principal_id"] = 1
                    it["sugestao_escolhida_id"] = None
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

    fechar_etapa(pid, "atribuicao")
    abrir_etapa(pid, ETAPA_DE.get(proximo, proximo), quem)
    log_acao(pid, quem, "atribuicao",
             {"responsavel": responsavel, "destino": proximo, "ajuste_manual": ajuste})

    nomes_resp = [n.strip() for n in (responsavel or "").split("/") if n.strip()]
    destinatarios = _emails_por_nomes(nomes_resp)
    if destinatarios:
        background_tasks.add_task(
            enviar_email,
            destinatarios=destinatarios,
            assunto=f"📋 Novo pregão pra você — {p['orgao']} PE {p['numero']}",
            titulo="Você foi atribuído a um novo pregão",
            corpo_html=(
                f"<p><b>{p['orgao']}</b><br>PE {p['numero']}</p>"
                f"<p>{quem} confirmou a atribuição. Etapa atual: "
                f"<b>{STATUS_ROTULO.get(proximo, proximo)}</b>.</p>"
                + (f"<p style=\"color:#8a9aab;font-size:12.5px\">Motivo da rota: {rota['motivo']}</p>" if rota.get("motivo") else "")
            ),
            evento="atribuicao_confirmada",
            cta_texto="Abrir o Gestor de Pregões",
            cta_url=INTRANET_BASE_URL,
        )
    return {"ok": True, "status": proximo, "responsavel": responsavel}


@router.post("/pregoes/{pid}/sugestoes")
def salvar_sugestoes(pid: int, dados: SugestoesIn, user=Depends(usuario_logado)):
    """
    Etapa "Definir 3 sugestões" (Allan). Para cada item que fornecemos e foi
    aprovado, grava exatamente 3 sugestões (fabricante + modelo), com uma
    marcada como principal. Ao concluir, o pregão vai para 'em_cotacao'.
    """
    quem = user["nome"]
    exige_papel(user, "definir_sugestoes")
    sug_por_item = {s.id: s.sugestoes for s in dados.itens}

    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")

        # itens que precisam de sugestão: fornecemos e não reprovados
        alvo = [it for it in p["itens"]
                if it.get("fornecemos") and it["validado"] != "reprovado"]

        for it in alvo:
            sugs = sug_por_item.get(it["id"])
            if sugs is None:
                raise HTTPException(status_code=400,
                    detail=f"Faltam as sugestões do item {it['numero_item']}.")
            validas = [s for s in sugs if s.fabricante.strip() and s.modelo.strip()]
            if len(validas) < 1:
                raise HTTPException(status_code=400,
                    detail=f"Item {it['numero_item']}: informe ao menos 1 sugestão (fabricante e modelo).")
            if len(validas) > 3:
                validas = validas[:3]
            # principal: se nenhuma marcada, a 1ª vira principal automaticamente
            n_princ = sum(1 for s in validas if s.principal)
            if n_princ == 0:
                validas[0].principal = True
            elif n_princ > 1:
                raise HTTPException(status_code=400,
                    detail=f"Item {it['numero_item']}: marque só 1 sugestão como principal.")
            it["sugestoes"] = [
                {"id": i + 1, "fabricante": s.fabricante.strip(),
                 "modelo": s.modelo.strip(), "principal": bool(s.principal)}
                for i, s in enumerate(validas)
            ]
            princ = next((s for s in it["sugestoes"] if s["principal"]), None)
            it["sugestao_escolhida_id"] = None   # a escolha final é na cotação
            it["sugestao_principal_id"] = princ["id"] if princ else None

        p["status"] = "em_cotacao"
        _limpar_devolucao(p)
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

    fechar_etapa(pid, "definir_sugestoes")
    abrir_etapa(pid, "cotacao", quem)
    log_acao(pid, quem, "definir_sugestoes", {"itens": len(alvo)})
    return {"ok": True, "status": "em_cotacao", "itens": len(alvo)}


# ---------------------------------------------------------------------------
# Cotação (Rafael / Pedro / Osvair) — valor total com impostos por sugestão,
# trava de teto, escolha da vencedora e trava por item. NÃO gera a planilha
# do Effecti aqui: a geração do .xlsx + datasheet acontece na Entrega.
# ---------------------------------------------------------------------------
def _itens_para_cotar(p):
    """Itens que entram na cotação: fornecemos, não reprovados e não declinados
    na própria fase de cotação."""
    return [it for it in p["itens"]
            if it.get("fornecemos") and it.get("validado") != "reprovado"
            and not it.get("cotacao_declinada")]

def _sug_map(it):
    return {s["id"]: s for s in it.get("sugestoes", [])}


@router.post("/pregoes/{pid}/cotacao")
def salvar_cotacao(pid: int, dados: CotacaoIn, user=Depends(usuario_logado)):
    """
    Salva o estado da cotação (rascunho): grava o 'valor_total_impostos' de cada
    sugestão, a sugestão escolhida e se o item está travado. Valida a trava de
    teto — não deixa travar uma escolha acima do teto nem sem preço. NÃO muda o
    status: o operador pode salvar e voltar. O envio à revisão é rota separada.
    """
    quem = user["nome"]
    cot_por_item = {c.id: c for c in dados.itens}

    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        exige_cotacao(user, p)
        if p["status"] not in ("em_cotacao", "em_revisao"):
            raise HTTPException(status_code=409,
                detail="Este pregão não está em cotação.")

        for it in _itens_para_cotar(p):
            cot = cot_por_item.get(it["id"])
            if cot is None:
                continue  # item sem alteração enviada; mantém o que está
            teto = it.get("teto_unitario")
            smap = _sug_map(it)
            # grava os preços informados (valida faixa: > 0)
            for cs in cot.sugestoes:
                s = smap.get(cs.id)
                if not s:
                    continue
                v = cs.valor_total_impostos
                if v is not None and v <= 0:
                    raise HTTPException(status_code=400,
                        detail=f"Item {it['numero_item']}: valor inválido.")
                s["valor_total_impostos"] = v
                if cs.distribuidor is not None:
                    s["distribuidor"] = cs.distribuidor.strip() or None
            # escolha da vencedora (se enviada, precisa existir)
            it["sugestao_escolhida_id"] = cot.sugestao_escolhida_id
            # trava do item: só permite com escolha, preço válido e dentro do teto
            if cot.travada:
                esc = smap.get(cot.sugestao_escolhida_id or -1)
                if not esc:
                    raise HTTPException(status_code=400,
                        detail=f"Item {it['numero_item']}: escolha a sugestão vencedora antes de travar.")
                v = esc.get("valor_total_impostos")
                if v is None:
                    raise HTTPException(status_code=400,
                        detail=f"Item {it['numero_item']}: informe o valor total com impostos da vencedora.")
                # teto 0/vazio = SEM teto. Havendo teto e a vencedora passando,
                # NÃO bloqueia: apenas marca (a decisão de participar é na revisão).
                tem_teto = teto is not None and teto > 0
                it["acima_teto"] = bool(tem_teto and v > teto)
                it["cotacao_travada"] = True
            else:
                it["cotacao_travada"] = False

        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

    log_acao(pid, quem, "salvou_cotacao",
             {"travados": sum(1 for it in _itens_para_cotar(p) if it.get("cotacao_travada"))})
    return {"ok": True}


def _norm(s):
    """Normaliza fabricante/modelo p/ casar: minúsculas, sem espaços extras."""
    return " ".join(str(s or "").lower().split())

@router.get("/pregoes/{pid}/cotacao/historico")
def cotacao_historico(pid: int, user=Depends(usuario_logado)):
    """
    Para cada sugestão dos itens em cotação, devolve os registros do histórico
    que casam por FABRICANTE + MODELO (de qualquer pregão anterior). Alimenta o
    botão 'reaproveitar' da Cotação v2. Marca a origem (aprovado/declinado) e
    traz valor + distribuidor + de qual pregão veio, do mais recente ao mais antigo.
    """
    pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
    p = next((x for x in pregoes if x["id"] == pid), None)
    if not p:
        raise HTTPException(status_code=404, detail="Pregão não encontrado")
    exige_cotacao(user, p)
    hist = _ler(HISTORICO_PATH, HISTORICO_VAZIO)

    # indexa o histórico por (fabricante, modelo) -> lista de ocorrências
    idx = {}
    for h in hist:
        if h.get("pregao_id") == pid:
            continue  # não reaproveita do próprio pregão
        # cada registro pode ter várias sugestões cotadas
        entradas = h.get("cotadas") or [{
            "fabricante": h.get("fabricante"), "modelo": h.get("modelo"),
            "valor_total_impostos": h.get("valor_total_impostos"),
            "distribuidor": h.get("distribuidor"), "vencedora": True,
        }]
        for e in entradas:
            if e.get("valor_total_impostos") is None:
                continue
            chave = (_norm(e.get("fabricante")), _norm(e.get("modelo")))
            if not chave[0] or not chave[1]:
                continue
            idx.setdefault(chave, []).append({
                "valor_total_impostos": e.get("valor_total_impostos"),
                "distribuidor": e.get("distribuidor"),
                "orgao": h.get("orgao"), "numero": h.get("numero"),
                "resultado": h.get("resultado"),  # aprovado | declinado
                "vencedora": e.get("vencedora", False),
                "em": h.get("em"),
            })

    # monta a resposta por item/sugestão da cotação atual
    saida = []
    for it in _itens_para_cotar(p):
        sugs = []
        for s in it.get("sugestoes", []):
            chave = (_norm(s.get("fabricante")), _norm(s.get("modelo")))
            ocorr = sorted(idx.get(chave, []),
                           key=lambda x: (x.get("em") or ""), reverse=True)
            sugs.append({"sugestao_id": s["id"],
                         "fabricante": s.get("fabricante"), "modelo": s.get("modelo"),
                         "ocorrencias": ocorr})
        saida.append({"item_id": it["id"], "numero_item": it.get("numero_item"),
                      "sugestoes": sugs})
    return {"itens": saida}


def _gerar_planilha_cotacao_bytes(pregao: dict, itens: list) -> bytes:
    """Monta do zero uma planilha de cotação (RFQ): 1 linha por item, com as
    sugestões (fabricante/modelo) lado a lado — até o maior número de
    sugestões entre os itens exportados. A coluna de valor de cada sugestão
    sai sempre em branco (destacada em amarelo): é pra pedir cotação nova,
    não pra expor preço já lançado internamente."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    max_sugs = max((len(it.get("sugestoes") or []) for it in itens), default=1) or 1

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cotação"

    FONTE = "Arial"
    f_titulo = Font(name=FONTE, size=13, bold=True, color="164023")
    f_sub = Font(name=FONTE, size=10, color="4A5560")
    f_head = Font(name=FONTE, size=10, bold=True, color="FFFFFF")
    f_cel = Font(name=FONTE, size=10)
    fill_head = PatternFill("solid", fgColor="164023")
    fill_valor = PatternFill("solid", fgColor="FFF2CC")
    lado = Side(style="thin", color="D9D9D9")
    borda = Border(left=lado, right=lado, top=lado, bottom=lado)
    align_wrap = Alignment(vertical="top", wrap_text=True)

    NCOLS_FIXAS = 4  # Nº Item, Descrição, Especificação técnica, Quantidade
    total_cols = NCOLS_FIXAS + max_sugs * 3

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    ws.cell(1, 1, f"Cotação — {pregao.get('orgao','')} — PE {pregao.get('numero','')}").font = f_titulo
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=total_cols)
    ws.cell(2, 1, f"Sessão: {pregao.get('data_sessao') or '—'}").font = f_sub
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=total_cols)
    ws.cell(3, 1, "Preencha o valor total com impostos de cada sugestão cotada (colunas em amarelo).").font = f_sub

    HEADER_ROW = 5
    headers = ["Nº Item", "Descrição", "Especificação técnica", "Quantidade"]
    for i in range(1, max_sugs + 1):
        headers += [f"Sugestão {i} — Fabricante", f"Sugestão {i} — Modelo", f"Sugestão {i} — Valor (R$)"]
    for ci, h in enumerate(headers, start=1):
        c = ws.cell(HEADER_ROW, ci, h)
        c.font = f_head; c.fill = fill_head; c.border = borda
        c.alignment = Alignment(vertical="center", wrap_text=True)

    r = HEADER_ROW + 1
    for it in itens:
        sugs = it.get("sugestoes") or []
        ws.cell(r, 1, it.get("numero_item") or "")
        ws.cell(r, 2, it.get("descricao") or "")
        ws.cell(r, 3, it.get("especificacao_tecnica") or "")
        ws.cell(r, 4, it.get("quantidade"))
        col = NCOLS_FIXAS + 1
        for i in range(max_sugs):
            s = sugs[i] if i < len(sugs) else None
            fab = (s.get("fabricante") or "") if s else ""
            mod = (s.get("modelo") or "") if s else ""
            if s and s.get("principal"):
                mod = (mod + " (principal)").strip()
            ws.cell(r, col, fab)
            ws.cell(r, col + 1, mod)
            vcell = ws.cell(r, col + 2, None)
            vcell.fill = fill_valor
            vcell.number_format = "#,##0.00"
            col += 3
        for cc in range(1, total_cols + 1):
            cell = ws.cell(r, cc)
            cell.font = f_cel; cell.border = borda
            if cc in (2, 3):
                cell.alignment = align_wrap
        r += 1

    widths = {1: 9, 2: 34, 3: 40, 4: 11}
    for i in range(max_sugs):
        base = NCOLS_FIXAS + 1 + i * 3
        widths[base] = 20; widths[base + 1] = 24; widths[base + 2] = 16
    for col_idx, w in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = w
    ws.row_dimensions[HEADER_ROW].height = 28

    ws.freeze_panes = f"E{HEADER_ROW + 1}"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def _gerar_planilha_itens_cotados_bytes(pregao: dict, itens: list) -> bytes:
    """Planilha enxuta com a sugestão VENCEDORA já cotada de cada item — para
    consulta em disputa / pronto p/ Effecti, etapas onde a tela de Cotação
    não fica mais acessível. 1 linha por item: nº, descrição, quantidade,
    fabricante/modelo e valor total com impostos da sugestão escolhida."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Itens cotados"

    FONTE = "Arial"
    f_titulo = Font(name=FONTE, size=13, bold=True, color="164023")
    f_sub = Font(name=FONTE, size=10, color="4A5560")
    f_head = Font(name=FONTE, size=10, bold=True, color="FFFFFF")
    f_cel = Font(name=FONTE, size=10)
    fill_head = PatternFill("solid", fgColor="164023")
    lado = Side(style="thin", color="D9D9D9")
    borda = Border(left=lado, right=lado, top=lado, bottom=lado)
    align_wrap = Alignment(vertical="top", wrap_text=True)

    headers = ["Nº Item", "Descrição", "Quantidade", "Fabricante", "Modelo",
               "Valor total c/ impostos (R$)", "Distribuidor"]
    total_cols = len(headers)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    ws.cell(1, 1, f"Itens cotados — {pregao.get('orgao','')} — PE {pregao.get('numero','')}").font = f_titulo
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=total_cols)
    ws.cell(2, 1, f"Sessão: {pregao.get('data_sessao') or '—'}").font = f_sub

    HEADER_ROW = 4
    for ci, h in enumerate(headers, start=1):
        c = ws.cell(HEADER_ROW, ci, h)
        c.font = f_head; c.fill = fill_head; c.border = borda
        c.alignment = Alignment(vertical="center", wrap_text=True)

    r = HEADER_ROW + 1
    for it in itens:
        smap = _sug_map(it)
        esc = smap.get(it.get("sugestao_escolhida_id"))
        ws.cell(r, 1, it.get("numero_item") or "")
        ws.cell(r, 2, it.get("descricao") or "")
        ws.cell(r, 3, it.get("quantidade"))
        ws.cell(r, 4, (esc.get("fabricante") if esc else "") or "")
        ws.cell(r, 5, (esc.get("modelo") if esc else "") or "")
        vcell = ws.cell(r, 6, esc.get("valor_total_impostos") if esc else None)
        vcell.number_format = "#,##0.00"
        ws.cell(r, 7, (esc.get("distribuidor") if esc else "") or "")
        for cc in range(1, total_cols + 1):
            cell = ws.cell(r, cc)
            cell.font = f_cel; cell.border = borda
            if cc == 2:
                cell.alignment = align_wrap
        r += 1

    widths = {1: 9, 2: 40, 3: 11, 4: 22, 5: 26, 6: 22, 7: 20}
    for col_idx, w in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = w
    ws.row_dimensions[HEADER_ROW].height = 24

    ws.freeze_panes = f"A{HEADER_ROW + 1}"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


@router.get("/pregoes/{pid}/cotacao/exportar-itens")
def exportar_itens_cotados(pid: int, user=Depends(usuario_logado)):
    """Exporta os itens já cotados com a sugestão vencedora e o valor lançado —
    diferente de /cotacao/exportar (que sai em branco, pra pedir cotação nova
    externamente). Disponível também em disputa e pronto p/ Effecti, etapas
    em que a tela de Cotação deixa de estar acessível. Qualquer usuário
    logado pode exportar (mesmo padrão do 'Ver triagem')."""
    pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
    p = next((x for x in pregoes if x["id"] == pid), None)
    if not p:
        raise HTTPException(status_code=404, detail="Pregão não encontrado")
    if p["status"] not in ("em_cotacao", "em_revisao", "pronto_effecti", "em_disputa"):
        raise HTTPException(status_code=409,
            detail="Este pregão não tem itens cotados disponíveis para exportar.")
    itens = _itens_para_cotar(p)
    if not itens:
        raise HTTPException(status_code=400, detail="Não há itens cotados neste pregão.")

    xlsx_bytes = _gerar_planilha_itens_cotados_bytes(p, itens)
    numero = _slug(p.get("numero") or pid)
    nome = f"itens_cotados_PE{numero}.xlsx"
    return StreamingResponse(
        iter([xlsx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.get("/pregoes/{pid}/cotacao/exportar")
def exportar_cotacao(pid: int, user=Depends(usuario_logado)):
    """Exporta os itens em cotação (os mesmos da tela 'montar proposta') com
    as sugestões lado a lado, prontos pra mandar pra fora e pedir preço.
    1 linha por item; a coluna de valor sai sempre em branco (não expõe
    preço já lançado internamente)."""
    pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
    p = next((x for x in pregoes if x["id"] == pid), None)
    if not p:
        raise HTTPException(status_code=404, detail="Pregão não encontrado")
    exige_cotacao(user, p)
    itens = _itens_para_cotar(p)
    if not itens:
        raise HTTPException(status_code=400, detail="Não há itens para cotar neste pregão.")

    xlsx_bytes = _gerar_planilha_cotacao_bytes(p, itens)
    numero = _slug(p.get("numero") or pid)
    nome = f"cotacao_PE{numero}.xlsx"
    return StreamingResponse(
        iter([xlsx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.post("/pregoes/{pid}/cotacao/editar-sugestoes")
def editar_sugestoes_cotacao(pid: int, dados: EditarSugestoesIn, user=Depends(usuario_logado)):
    """Corrige fabricante/modelo das sugestões de um item na fase de cotação.
    Só coordenador (ex.: modelo descontinuado). Trocar o modelo/fabricante limpa
    o preço daquela sugestão (é outro produto) e destrava/desmarca se ela era a
    escolhida."""
    quem = user["nome"]
    _exige_coordenador(user)
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        if p["status"] not in ("em_cotacao", "em_revisao"):
            raise HTTPException(status_code=409, detail="Este pregão não está em cotação.")
        it = next((x for x in p.get("itens", []) if x["id"] == dados.item_id), None)
        if not it:
            raise HTTPException(status_code=404, detail="Item não encontrado")
        smap = {s["id"]: s for s in it.get("sugestoes", [])}
        for ed in dados.sugestoes:
            s = smap.get(ed.id)
            if not s:
                continue
            mudou = False
            if ed.fabricante is not None and ed.fabricante.strip() != (s.get("fabricante") or ""):
                s["fabricante"] = ed.fabricante.strip(); mudou = True
            if ed.modelo is not None and ed.modelo.strip() != (s.get("modelo") or ""):
                s["modelo"] = ed.modelo.strip(); mudou = True
            if mudou:
                # é outro produto: zera preço/distribuidor e, se era a escolhida, desmarca
                s["valor_total_impostos"] = None
                s["distribuidor"] = None
                if it.get("sugestao_escolhida_id") == s["id"]:
                    it["sugestao_escolhida_id"] = None
                    it["cotacao_travada"] = False
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    log_acao(pid, quem, "editou_sugestoes_cotacao", {"item": dados.item_id})
    return {"ok": True}


@router.post("/pregoes/{pid}/cotacao/adicionar-sugestao")
def adicionar_sugestao_cotacao(pid: int, dados: AddSugestaoIn, user=Depends(usuario_logado)):
    """Adiciona uma nova sugestão (linha) a um item na fase de cotação. Só
    coordenador. Nasce sem preço; o operador cota depois. Não vira principal."""
    quem = user["nome"]
    _exige_coordenador(user)
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        if p["status"] not in ("em_cotacao", "em_revisao"):
            raise HTTPException(status_code=409, detail="Este pregão não está em cotação.")
        it = next((x for x in p.get("itens", []) if x["id"] == dados.item_id), None)
        if not it:
            raise HTTPException(status_code=404, detail="Item não encontrado")
        sugs = it.setdefault("sugestoes", [])
        novo_id = (max((s["id"] for s in sugs), default=0) + 1)
        sugs.append({
            "id": novo_id,
            "fabricante": (dados.fabricante or "").strip(),
            "modelo": (dados.modelo or "").strip(),
            "principal": False,
            "valor_total_impostos": None, "distribuidor": None,
        })
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    log_acao(pid, quem, "adicionou_sugestao_cotacao", {"item": dados.item_id, "sug": novo_id})
    return {"ok": True, "sugestao_id": novo_id}


@router.post("/pregoes/{pid}/cotacao/declinar-item")
def declinar_item_cotacao(pid: int, dados: DeclinarItemIn, user=Depends(usuario_logado)):
    """Declina um item na fase de cotação, com motivo (ex.: todos os modelos
    descontinuados). Gera Lição (origem 'cotacao'). Se sobrar >=1 item cotável,
    o pregão segue; se todos os itens que fornecemos forem declinados, vira
    'nao_realizado'. Coordenador e operacional (quem cota)."""
    quem = user["nome"]
    if not (dados.motivo or "").strip():
        raise HTTPException(status_code=400, detail="Informe o motivo do declínio.")
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        exige_cotacao(user, p)
        if p["status"] != "em_cotacao":
            raise HTTPException(status_code=409, detail="Este pregão não está em cotação.")
        it = next((x for x in p.get("itens", []) if x["id"] == dados.item_id), None)
        if not it:
            raise HTTPException(status_code=404, detail="Item não encontrado")
        # marca o item como declinado nesta fase
        it["cotacao_declinada"] = True
        it["motivo_declinio_cotacao"] = dados.motivo.strip()
        it["cotacao_travada"] = False
        it["sugestao_escolhida_id"] = None

        # lição
        licoes = _ler(LICOES_PATH, LICOES_VAZIO)
        licoes.append({
            "id": _proximo_id(licoes), "pregao_id": pid,
            "orgao": p.get("orgao"), "numero": p.get("numero"),
            "origem": "cotacao", "numero_item": it.get("numero_item"),
            "descricao": it.get("descricao"), "motivo": dados.motivo.strip(),
            "por": quem, "em": AGORA(),
        })
        _escrever_atomico(licoes, LICOES_PATH)

        # se não sobra nenhum item cotável (fornecemos, não reprovado, não declinado), vira nao_realizado
        cotaveis = [x for x in p.get("itens", [])
                    if x.get("fornecemos") and x.get("validado") != "reprovado"
                    and not x.get("cotacao_declinada")]
        virou_nao_realizado = False
        if not cotaveis:
            p["status"] = "nao_realizado"
            p["resultado"] = "nao_participado"
            p["resultado_motivo"] = "Todos os itens declinados na cotação."
            p["resultado_em"] = AGORA(); p["resultado_por"] = quem
            virou_nao_realizado = True
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    if virou_nao_realizado:
        fechar_etapa(pid, "cotacao")
    log_acao(pid, quem, "declinou_item_cotacao",
             {"item": dados.item_id, "nao_realizado": virou_nao_realizado})
    return {"ok": True, "nao_realizado": virou_nao_realizado}


@router.post("/pregoes/{pid}/cotacao/datasheet")
async def upload_datasheet(pid: int, item_id: int = Form(...),
                           arquivo: UploadFile = File(...),
                           user=Depends(usuario_logado)):
    """
    Anexa o datasheet (PDF) do modelo aprovado de um item. Guardado em
    data/datasheets/<pregao>_<item>_<arquivo>. O caminho fica em
    it['datasheet_path'] para vincular na planilha do Effecti (etapa de Entrega).
    """
    quem = user["nome"]
    nome = (arquivo.filename or "").lower()
    if not nome.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="O datasheet deve ser um PDF.")
    conteudo = await arquivo.read()
    if len(conteudo) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo muito grande (máx. 25 MB).")

    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        exige_cotacao(user, p)
        it = next((x for x in p["itens"] if x["id"] == item_id), None)
        if not it:
            raise HTTPException(status_code=404, detail="Item não encontrado")

        DATASHEETS_DIR.mkdir(parents=True, exist_ok=True)
        seguro = "".join(c if c.isalnum() or c in "._-" else "_" for c in (arquivo.filename or "ficha.pdf"))
        destino = DATASHEETS_DIR / f"{pid}_{item_id}_{seguro}"
        # escrita atômica do binário
        fd, tmp = tempfile.mkstemp(dir=str(DATASHEETS_DIR), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(conteudo); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, destino)
        except Exception as e:
            if os.path.exists(tmp): os.remove(tmp)
            raise HTTPException(status_code=500, detail=f"Falha ao salvar datasheet: {e}")

        it["datasheet_path"] = str(destino.relative_to(BASE))
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

    log_acao(pid, quem, "anexou_datasheet", {"item": item_id, "arquivo": arquivo.filename})
    return {"ok": True, "datasheet_path": it["datasheet_path"]}


@router.post("/pregoes/{pid}/cotacao/enviar")
def enviar_cotacao(pid: int, background_tasks: BackgroundTasks, user=Depends(usuario_logado)):
    """
    Fecha a cotação e envia à revisão (Allan). Exige que TODOS os itens a cotar
    estejam travados, com vencedora dentro do teto e datasheet anexado. A revisão
    é obrigatória antes da entrega ao Effecti.
    """
    quem = user["nome"]
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        exige_cotacao(user, p)
        if p["status"] != "em_cotacao":
            raise HTTPException(status_code=409, detail="Este pregão não está em cotação.")

        alvo = _itens_para_cotar(p)
        for it in alvo:
            if not it.get("cotacao_travada"):
                raise HTTPException(status_code=400,
                    detail=f"Item {it['numero_item']}: trave a decisão antes de enviar.")
            smap = _sug_map(it)
            esc = smap.get(it.get("sugestao_escolhida_id") or -1)
            v = esc.get("valor_total_impostos") if esc else None
            teto = it.get("teto_unitario")
            if not esc or v is None:
                raise HTTPException(status_code=400,
                    detail=f"Item {it['numero_item']}: cotação incompleta.")
            # acima do teto NÃO bloqueia mais: a decisão de participar é na revisão.
            if not it.get("datasheet_path"):
                raise HTTPException(status_code=400,
                    detail=f"Item {it['numero_item']}: anexe o datasheet do modelo aprovado.")

        p["status"] = "em_revisao"
        _limpar_devolucao(p)
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

    fechar_etapa(pid, "cotacao")
    abrir_etapa(pid, "revisao", quem)
    log_acao(pid, quem, "enviou_cotacao", {"itens": len(alvo)})

    destinatarios = _emails_coordenadores()
    if destinatarios:
        background_tasks.add_task(
            enviar_email,
            destinatarios=destinatarios,
            assunto=f"🔎 Cotação pronta pra revisão — {p['orgao']} PE {p['numero']}",
            titulo="Uma cotação está esperando revisão",
            corpo_html=(
                f"<p><b>{p['orgao']}</b><br>PE {p['numero']}</p>"
                f"<p>{quem} enviou a cotação ({len(alvo)} item(ns)) para revisão.</p>"
            ),
            evento="cotacao_enviada_revisao",
            cta_texto="Abrir o Gestor de Pregões",
            cta_url=INTRANET_BASE_URL,
        )
    return {"ok": True, "status": "em_revisao", "itens": len(alvo)}


# ---------------------------------------------------------------------------
# Revisão (Allan) — conferência item a item antes do Effecti. Cada item é
# aprovado ou declinado (com motivo). Sobrando >=1 aprovado, o pregão segue
# para 'pronto_effecti' SÓ com os aprovados; se nenhum aprovado, cai em
# 'nao_realizado'. Itens declinados e o pregão declinado geram Lição.
# Todo item encerrado (aprovado OU declinado) é gravado no HISTÓRICO, para a
# Cotação de pregões futuros oferecer reaproveitamento.
# ---------------------------------------------------------------------------
def _registrar_historico(p, it, resultado, quem):
    """Grava um item encerrado no histórico de itens (base p/ reuso futuro).
    Registra a vencedora (com distribuidor) e também as demais sugestões que
    tiveram valor — o reaproveitamento casa por fabricante+modelo, então uma
    sugestão que foi 2ª opção aqui pode ser a vencedora de outro pregão."""
    esc = None
    for s in it.get("sugestoes", []):
        if s["id"] == it.get("sugestao_escolhida_id"):
            esc = s
            break
    # todas as sugestões cotadas (fabricante+modelo+valor+distribuidor)
    cotadas = [{
        "fabricante": s.get("fabricante"), "modelo": s.get("modelo"),
        "valor_total_impostos": s.get("valor_total_impostos"),
        "distribuidor": s.get("distribuidor"),
        "vencedora": s["id"] == it.get("sugestao_escolhida_id"),
    } for s in it.get("sugestoes", []) if s.get("valor_total_impostos") is not None]
    hist = _ler(HISTORICO_PATH, HISTORICO_VAZIO)
    hist.append({
        "id": _proximo_id(hist),
        "pregao_id": p["id"], "orgao": p.get("orgao"), "numero": p.get("numero"),
        "tipo": p.get("tipo"),
        "numero_item": it.get("numero_item"), "descricao": it.get("descricao"),
        "especificacao_tecnica": it.get("especificacao_tecnica"),
        "quantidade": it.get("quantidade"), "teto_unitario": it.get("teto_unitario"),
        "fabricante": esc.get("fabricante") if esc else None,
        "modelo": esc.get("modelo") if esc else None,
        "valor_total_impostos": esc.get("valor_total_impostos") if esc else None,
        "distribuidor": esc.get("distribuidor") if esc else None,
        "cotadas": cotadas,
        "datasheet_path": it.get("datasheet_path"),
        "resultado": resultado,          # "aprovado" | "declinado"
        "por": quem, "em": AGORA(),
    })
    _escrever_atomico(hist, HISTORICO_PATH)


@router.post("/pregoes/{pid}/revisao")
def revisar(pid: int, dados: RevisaoIn, user=Depends(usuario_logado)):
    """
    Revisão obrigatória (Allan) antes do Effecti. Decisão item a item.
    - Item acima do teto NÃO pode ser aprovado (só declinado).
    - Declinar item exige motivo. Item declinado sai da proposta, vira Lição e histórico.
    - Sobra >=1 aprovado -> 'pronto_effecti' (só aprovados); nenhum -> 'nao_realizado'.
    - Todo item encerrado é gravado no histórico (aprovado ou declinado).
    """
    quem = user["nome"]
    exige_papel(user, "revisao")
    dec_por_item = {d.id: d for d in dados.itens}

    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        if p["status"] != "em_revisao":
            raise HTTPException(status_code=409, detail="Este pregão não está em revisão.")

        alvo = _itens_para_cotar(p)  # itens que fornecemos e não reprovados na validação
        # exige decisão para TODOS os itens em revisão
        for it in alvo:
            d = dec_por_item.get(it["id"])
            if d is None or d.decisao not in ("aprovado", "declinado"):
                raise HTTPException(status_code=400,
                    detail=f"Item {it['numero_item']}: decida aprovar ou declinar.")
            if d.decisao == "declinado" and not (d.motivo or "").strip():
                raise HTTPException(status_code=400,
                    detail=f"Item {it['numero_item']}: informe o motivo do declínio.")
            # cotação precisa estar completa para aprovar (teto NÃO bloqueia:
            # a decisão de participar acima do teto é do coordenador na revisão)
            if d.decisao == "aprovado":
                esc = next((s for s in it.get("sugestoes", [])
                            if s["id"] == it.get("sugestao_escolhida_id")), None)
                v = esc.get("valor_total_impostos") if esc else None
                if not esc or v is None:
                    raise HTTPException(status_code=400,
                        detail=f"Item {it['numero_item']}: cotação incompleta, não pode aprovar.")

        aprovados = [it for it in alvo if dec_por_item[it["id"]].decisao == "aprovado"]
        declinados = [it for it in alvo if dec_por_item[it["id"]].decisao == "declinado"]

        # aplica as decisões nos itens
        for it in alvo:
            d = dec_por_item[it["id"]]
            it["revisao"] = d.decisao
            it["motivo_declinio_revisao"] = (d.motivo or "").strip() or None

        # Itens declinados NA COTAÇÃO (fora do "alvo" — não pedem decisão).
        # Opcionalmente, o coordenador pode aprová-los "mesmo assim" aqui na
        # revisão, pra participar da disputa e acompanhar o que os
        # concorrentes oferecem nesses itens (mesmo sem chance real de
        # ganhar). O motivo do declínio original NUNCA é apagado — continua
        # valendo como Lição, mesmo que o item seja aprovado agora.
        declinados_cotacao = [it for it in p["itens"]
                               if it.get("fornecemos") and it.get("validado") != "reprovado"
                               and it.get("cotacao_declinada")]
        aprovados_apesar_declinio = []
        for it in declinados_cotacao:
            d = dec_por_item.get(it["id"])
            if d is None or d.decisao != "aprovado":
                continue  # sem decisão ou decisão="declinado" -> nada muda, fica só informativo
            if d.sugestao_id is not None:
                esc = next((s for s in it.get("sugestoes", []) if s["id"] == d.sugestao_id), None)
                if not esc or esc.get("valor_total_impostos") is None:
                    raise HTTPException(status_code=400,
                        detail=f"Item {it['numero_item']}: a sugestão escolhida não tem valor. "
                               f"Informe um valor manual pra aprovar mesmo assim.")
                it["sugestao_escolhida_id"] = esc["id"]
            else:
                if d.valor is None or d.valor < 0:
                    raise HTTPException(status_code=400,
                        detail=f"Item {it['numero_item']}: informe o valor pra aprovar mesmo assim.")
                modelo = (d.modelo or "").strip() or (it.get("modelo_referencia") or "").strip()
                if not modelo:
                    raise HTTPException(status_code=400,
                        detail=f"Item {it['numero_item']}: informe ao menos o modelo "
                               f"(não há modelo de referência cadastrado no edital).")
                sugs = it.setdefault("sugestoes", [])
                novo_id = max([s["id"] for s in sugs], default=0) + 1
                sugs.append({"id": novo_id, "fabricante": (d.fabricante or "").strip(),
                             "modelo": modelo, "principal": True,
                             "valor_total_impostos": d.valor, "distribuidor": None})
                it["sugestao_escolhida_id"] = novo_id
            it["revisao"] = "aprovado"
            it["motivo_declinio_revisao"] = None
            it["cotacao_travada"] = True
            it["aprovado_apesar_declinio_cotacao"] = True  # sinaliza a origem, pra exibição
            aprovados_apesar_declinio.append(it)

        aprovados = aprovados + aprovados_apesar_declinio

        # destino do pregão
        if aprovados:
            novo = "pronto_effecti"
        else:
            novo = "nao_realizado"
        p["status"] = novo
        p["revisado_por"] = quem
        p["revisado_em"] = AGORA()
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

        # histórico: todo item encerrado (aprovado ou declinado)
        for it in aprovados:
            _registrar_historico(p, it, "aprovado", quem)
        for it in declinados:
            _registrar_historico(p, it, "declinado", quem)

        # lições: uma por item declinado; se o pregão inteiro caiu, uma lição do pregão
        licoes = _ler(LICOES_PATH, LICOES_VAZIO)
        for it in declinados:
            licoes.append({
                "id": _proximo_id(licoes), "pregao_id": pid,
                "orgao": p.get("orgao"), "numero": p.get("numero"),
                "origem": "revisao", "numero_item": it.get("numero_item"),
                "descricao": it.get("descricao"),
                "motivo": dec_por_item[it["id"]].motivo,
                "por": quem, "em": AGORA(),
            })
        if not aprovados:
            licoes.append({
                "id": _proximo_id(licoes), "pregao_id": pid,
                "orgao": p.get("orgao"), "numero": p.get("numero"),
                "origem": "revisao", "numero_item": None,
                "descricao": "Pregão não realizado (todos os itens declinados)",
                "motivo": (dados.motivo_geral or "").strip()
                          or "Nenhum item viável dentro do teto.",
                "por": quem, "em": AGORA(),
            })
        _escrever_atomico(licoes, LICOES_PATH)

    fechar_etapa(pid, "revisao")
    if novo == "pronto_effecti":
        abrir_etapa(pid, "pronto", quem)
    log_acao(pid, quem, "revisao",
             {"aprovados": len(aprovados), "declinados": len(declinados), "status": novo})
    return {"ok": True, "status": novo,
            "aprovados": len(aprovados), "declinados": len(declinados)}


# ---------------------------------------------------------------------------
# Entrega (pronto_effecti) — gera a planilha do Effecti (1 linha por item
# APROVADO) no layout do modelo padrão e empacota, junto dos datasheets dos
# itens aprovados, num único .zip para importar no Effecti.
# ---------------------------------------------------------------------------
_PLAN_PRIMEIRA_LINHA = 2  # dados começam na linha 2 (linha 1 = cabeçalho)

def _itens_aprovados_entrega(p):
    """Itens que entram na entrega: aprovados na revisão (com vencedora)."""
    out = []
    for it in p["itens"]:
        if it.get("revisao") != "aprovado":
            continue
        esc = next((s for s in it.get("sugestoes", [])
                    if s["id"] == it.get("sugestao_escolhida_id")), None)
        out.append({
            "numero_item": it.get("numero_item"),
            "fabricante": esc.get("fabricante") if esc else "",
            "modelo": esc.get("modelo") if esc else "",
            "especificacao_tecnica": it.get("especificacao_tecnica") or it.get("descricao") or "",
            "quantidade": it.get("quantidade"),
            "valor_cotado": esc.get("valor_total_impostos") if esc else None,
            "teto_unitario": it.get("teto_unitario"),
            "datasheet_path": it.get("datasheet_path"),
        })
    return out


def _gerar_planilha_bytes(itens_aprovados):
    """Copia o modelo do Effecti e preenche 1 linha por item aprovado.
    Retorna os bytes do .xlsx. Preserva formatação e fórmulas do modelo."""
    try:
        import openpyxl
    except Exception:
        raise HTTPException(status_code=500,
            detail="Gerador de planilha indisponível (falta openpyxl?).")
    if not MODELO_EFFECTI.exists():
        raise HTTPException(status_code=500,
            detail="Modelo da planilha do Effecti não encontrado em data/modelo_effecti.xlsx.")

    wb = openpyxl.load_workbook(MODELO_EFFECTI)
    ws = wb["Proposta"]
    R0 = _PLAN_PRIMEIRA_LINHA

    # guarda o estilo da linha-modelo de dado (linha 2) p/ replicar
    estilo = {}
    for col in "ABCDEFGHIJ":
        c = ws[f"{col}{R0}"]
        estilo[col] = {"font": copy(c.font), "fill": copy(c.fill),
                       "border": copy(c.border), "align": copy(c.alignment),
                       "fmt": c.number_format}

    # limpa linhas de dados antigas do modelo (preserva cabeçalho e J1)
    for r in range(R0, ws.max_row + 1):
        for col in "ABCDEFGHIJ":
            if col == "J" and r == 1:
                continue
            ws[f"{col}{r}"] = None

    r = R0
    for it in itens_aprovados:
        ws[f"A{r}"] = str(it.get("numero_item", ""))
        ws[f"B{r}"] = it.get("fabricante") or ""
        ws[f"C{r}"] = it.get("modelo") or ""
        ws[f"D{r}"] = it.get("especificacao_tecnica") or ""
        ws[f"E{r}"] = it.get("quantidade")
        ws[f"F{r}"] = it.get("valor_cotado")
        ws[f"G{r}"] = f"=(E{r} * F{r})"
        if it.get("teto_unitario") is not None:
            ws[f"H{r}"] = it.get("teto_unitario")
            ws[f"I{r}"] = f"=H{r}*$J$1"
        for col in "ABCDEFGHIJ":
            cell = ws[f"{col}{r}"]; st = estilo[col]
            cell.font = copy(st["font"]); cell.fill = copy(st["fill"])
            cell.border = copy(st["border"]); cell.alignment = copy(st["align"])
            cell.number_format = st["fmt"]
        r += 1

    if ws["J1"].value is None:
        ws["J1"] = 0.6

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def _slug(txt):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in str(txt or ""))


@router.get("/pregoes/{pid}/entrega")
def gerar_entrega(pid: int, user=Depends(usuario_logado)):
    """
    Gera o pacote de entrega do pregão (status 'pronto_effecti'): um .zip com a
    planilha do Effecti (1 linha por item aprovado) + os datasheets dos itens
    aprovados. Não muda o status; pode ser baixado quantas vezes precisar.
    """
    quem = user["nome"]
    exige_papel(user, "entrega")   # analistas (Thiago/Luana) e coordenador
    pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
    p = next((x for x in pregoes if x["id"] == pid), None)
    if not p:
        raise HTTPException(status_code=404, detail="Pregão não encontrado")
    if p["status"] != "pronto_effecti":
        raise HTTPException(status_code=409,
            detail="A entrega só é gerada quando o pregão está pronto para o Effecti.")

    itens = _itens_aprovados_entrega(p)
    if not itens:
        raise HTTPException(status_code=400, detail="Não há itens aprovados para entregar.")

    # rede de segurança: não gera planilha zerada. Item aprovado precisa de
    # quantidade > 0 e valor cotado. (Pregões antigos podem ter sido cadastrados
    # sem quantidade — agora o cadastro exige, mas a entrega também barra.)
    incompletos = [it["numero_item"] for it in itens
                   if not it.get("quantidade") or it.get("quantidade") <= 0
                   or it.get("valor_cotado") is None]
    if incompletos:
        raise HTTPException(status_code=400,
            detail=("Não é possível gerar a entrega: itens sem quantidade ou sem valor "
                    f"cotado: {', '.join(map(str, incompletos))}. Corrija o cadastro/cotação antes."))

    xlsx_bytes = _gerar_planilha_bytes(itens)

    # monta o zip: planilha + datasheets dos itens aprovados
    numero = _slug(p.get("numero") or pid)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"planilha_effecti_PE{numero}.xlsx", xlsx_bytes)
        for it in itens:
            ds = it.get("datasheet_path")
            if not ds:
                continue
            caminho = BASE / ds
            if caminho.exists():
                arc = f"datasheets/item_{_slug(it.get('numero_item'))}_{caminho.name}"
                z.write(caminho, arc)
    zbuf.seek(0)

    log_acao(pid, quem, "gerou_entrega", {"itens": len(itens)})
    nome_zip = f"entrega_PE{numero}.zip"
    return StreamingResponse(
        iter([zbuf.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nome_zip}"'},
    )


@router.get("/pregoes/{pid}/entrega/previa")
def previa_entrega(pid: int, user=Depends(usuario_logado)):
    """Prévia da entrega (sem gerar o arquivo): itens aprovados + total + quais
    têm datasheet. Alimenta a tela de Entrega antes de baixar o .zip."""
    exige_papel(user, "entrega")
    pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
    p = next((x for x in pregoes if x["id"] == pid), None)
    if not p:
        raise HTTPException(status_code=404, detail="Pregão não encontrado")
    itens = _itens_aprovados_entrega(p)
    total = sum((it["valor_cotado"] or 0) * (it["quantidade"] or 0) for it in itens)
    return {
        "pregao": {"id": p["id"], "orgao": p.get("orgao"), "numero": p.get("numero"),
                   "status": p.get("status")},
        "itens": [{
            "numero_item": it["numero_item"], "descricao": it["especificacao_tecnica"][:80],
            "fabricante": it["fabricante"], "modelo": it["modelo"],
            "quantidade": it["quantidade"], "valor_cotado": it["valor_cotado"],
            "valor_total": (it["valor_cotado"] or 0) * (it["quantidade"] or 0),
            "tem_datasheet": bool(it.get("datasheet_path")),
        } for it in itens],
        "total": total,
        "sem_datasheet": [it["numero_item"] for it in itens if not it.get("datasheet_path")],
        "incompletos": [it["numero_item"] for it in itens
                        if not it.get("quantidade") or it.get("quantidade") <= 0
                        or it.get("valor_cotado") is None],
    }


# ---------------------------------------------------------------------------
# Disputa e resultado (analista/coordenador) — fecha o ciclo do pregão.
# pronto_effecti -> em_disputa -> ganho | perdido | nao_realizado.
# 'perdido' e 'nao_realizado' exigem motivo e viram Lição (origem 'disputa').
# ---------------------------------------------------------------------------
class ResultadoIn(BaseModel):
    resultado: str                       # "ganho" | "perdido" | "nao_participado"
    motivo: Optional[str] = None

# rótulos amigáveis do resultado (para lição/log)
_RESULTADO_STATUS = {"ganho": "ganho", "perdido": "perdido", "nao_participado": "nao_realizado"}
_RESULTADO_ROTULO = {"ganho": "Ganho", "perdido": "Perdido", "nao_participado": "Não participado"}


@router.post("/pregoes/{pid}/disputa")
def mover_para_disputa(pid: int, user=Depends(usuario_logado)):
    """pronto_effecti -> em_disputa. Marca que a proposta foi enviada e o pregão
    está na sessão de disputa."""
    quem = user["nome"]
    exige_papel(user, "disputa")
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        if p["status"] != "pronto_effecti":
            raise HTTPException(status_code=409,
                detail="Só é possível mover para disputa um pregão pronto para o Effecti.")
        p["status"] = "em_disputa"
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)
    fechar_etapa(pid, "pronto")
    abrir_etapa(pid, "disputa", quem)
    log_acao(pid, quem, "mover_disputa", {})
    return {"ok": True, "status": "em_disputa"}


@router.post("/pregoes/{pid}/resultado")
def registrar_resultado(pid: int, dados: ResultadoIn, user=Depends(usuario_logado)):
    """
    Registra o resultado da disputa: ganho, perdido ou não participado.
    'perdido' e 'nao_participado' exigem motivo e geram Lição (origem 'disputa').
    """
    quem = user["nome"]
    exige_papel(user, "disputa")
    if dados.resultado not in _RESULTADO_STATUS:
        raise HTTPException(status_code=400, detail="Resultado inválido.")
    if dados.resultado in ("perdido", "nao_participado") and not (dados.motivo or "").strip():
        raise HTTPException(status_code=400,
            detail="Informe o motivo (perdido / não participado).")

    novo = _RESULTADO_STATUS[dados.resultado]
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        if p["status"] not in ("em_disputa", "pronto_effecti"):
            raise HTTPException(status_code=409,
                detail="O resultado só pode ser registrado após a proposta estar pronta/em disputa.")
        p["status"] = novo
        p["resultado"] = dados.resultado
        p["resultado_motivo"] = (dados.motivo or "").strip() or None
        p["resultado_em"] = AGORA()
        p["resultado_por"] = quem
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

        # perdido / não participado viram lição
        if dados.resultado in ("perdido", "nao_participado"):
            licoes = _ler(LICOES_PATH, LICOES_VAZIO)
            licoes.append({
                "id": _proximo_id(licoes), "pregao_id": pid,
                "orgao": p.get("orgao"), "numero": p.get("numero"),
                "origem": "disputa", "numero_item": None,
                "descricao": _RESULTADO_ROTULO[dados.resultado],
                "motivo": (dados.motivo or "").strip(),
                "por": quem, "em": AGORA(),
            })
            _escrever_atomico(licoes, LICOES_PATH)

    fechar_etapa(pid, "disputa")
    log_acao(pid, quem, "resultado", {"resultado": dados.resultado, "status": novo})
    return {"ok": True, "status": novo, "resultado": dados.resultado}



async def extrair_itens(arquivo: UploadFile = File(...), user=Depends(usuario_logado)):
    """
    Recebe um PDF de edital/termo de referência, extrai a tabela de itens e
    devolve a lista SEM salvar nada. O Thiago confere na tela e salva pelo
    fluxo normal de cadastro. A extração é imperfeita por natureza (cada órgão
    formata diferente) — por isso devolvemos também um 'aviso' quando algo
    veio incompleto.
    """
    nome = (arquivo.filename or "").lower()
    if not nome.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Envie um PDF do edital ou termo de referência.")
    conteudo = await arquivo.read()
    if len(conteudo) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo muito grande (máx. 25 MB).")
    # salva temporário e extrai
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    try:
        tmp.write(conteudo); tmp.flush(); tmp.close()
        try:
            from edital_extractor import extrair_itens_pdf
        except Exception:
            raise HTTPException(status_code=500,
                detail="Extrator indisponível no servidor (falta pdfplumber?).")
        try:
            resultado = extrair_itens_pdf(tmp.name)
        except Exception as e:
            raise HTTPException(status_code=422,
                detail=f"Não consegui ler este PDF ({type(e).__name__}). Cadastre manualmente.")
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass
    log_acao(0, user["nome"], "extraiu_itens_pdf",
             {"arquivo": arquivo.filename, "qtd": len(resultado["itens"])})
    return resultado


@router.post("/pregoes")
def criar_pregao(dados: NovoPregao, user=Depends(usuario_logado)):
    quem = user["nome"]  # AUTENTICADO — não confia em nome vindo do cliente
    # quantidade é dado do edital e obrigatória por item (fornece tudo ou zero,
    # nunca em branco). Barra na origem para não gerar planilha zerada lá na frente.
    sem_qtd = [it.numero_item for it in dados.itens
               if it.quantidade is None or it.quantidade <= 0]
    if sem_qtd:
        raise HTTPException(status_code=400,
            detail=f"Informe a quantidade (maior que zero) dos itens: {', '.join(map(str, sem_qtd))}.")
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        pid = _proximo_id(pregoes)
        itens = [{
            "id": idx, "numero_item": it.numero_item, "descricao": it.descricao,
            "quantidade": it.quantidade, "teto_unitario": it.teto_unitario,
            "fornecemos": it.fornecemos, "validado": "pendente", "motivo_reprova": None,
            "especificacao_tecnica": it.especificacao_tecnica,
            "modelo_referencia": it.modelo_referencia,
            "me_epp": it.me_epp or "ampla",
            "grupo_id": it.grupo_id,
            "incluido_adriano": False, "sugestoes": [],
            "sugestao_escolhida_id": None, "datasheet_path": None,
            "cotacao_travada": False,
        } for idx, it in enumerate(dados.itens, start=1)]
        novo = {
            "id": pid, "orgao": dados.orgao, "numero": dados.numero, "uasg": dados.uasg,
            "portal": dados.portal, "objeto": dados.objeto, "tipo": dados.tipo,
            "tem_referencia": dados.tem_referencia, "criterio": dados.criterio,
            "modo_disputa": dados.modo_disputa,
            "valor_estimado": None if dados.valor_sigiloso else dados.valor_estimado,
            "valor_sigiloso": dados.valor_sigiloso,
            "data_sessao": dados.data_sessao,
            "grupos": [{"id": g.id, "nome": g.nome, "teto_total": g.teto_total} for g in dados.grupos],
            "status": "triagem", "responsavel": None, "pasta_truenas": None,
            "itens": itens, "criado_por": quem, "criado_em": AGORA(), "atualizado_em": AGORA(),
        }
        pregoes.append(novo)
        _escrever_atomico(pregoes, PREGOES_PATH)

    abrir_etapa(pid, "cadastro", quem)
    fechar_etapa(pid, "cadastro")
    abrir_etapa(pid, "triagem", quem)
    log_acao(pid, quem, "criou_pregao", {"orgao": dados.orgao, "numero": dados.numero})
    return {"id": pid}


@router.patch("/pregoes/{pid}/status")
def mudar_status(pid: int, dados: MudarStatus, user=Depends(usuario_logado)):
    quem = user["nome"]
    if dados.status not in STATUS:
        raise HTTPException(status_code=400, detail="Status inválido")
    with _lock:
        pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
        p = next((x for x in pregoes if x["id"] == pid), None)
        if not p:
            raise HTTPException(status_code=404, detail="Pregão não encontrado")
        antigo = p["status"]
        p["status"] = dados.status
        p["atualizado_em"] = AGORA()
        _escrever_atomico(pregoes, PREGOES_PATH)

    if ETAPA_DE.get(antigo):
        fechar_etapa(pid, ETAPA_DE[antigo])
    if ETAPA_DE.get(dados.status):
        abrir_etapa(pid, ETAPA_DE[dados.status], quem, dados.nota)
    log_acao(pid, quem, "mudou_status", {"de": antigo, "para": dados.status})
    return {"ok": True, "status": dados.status}


# ordem canônica das etapas do fluxo (para exibir na ordem certa) e helpers de tempo
ORDEM_ETAPAS = ["triagem", "validacao", "atribuicao", "definir_sugestoes",
                "cotacao", "revisao", "pronto", "disputa"]
ROTULO_ETAPA = {
    "triagem": "Triagem", "validacao": "Validação (Adriano)",
    "atribuicao": "Atribuição (Rodrigo)", "definir_sugestoes": "Definir sugestões",
    "cotacao": "Cotação", "revisao": "Revisão", "pronto": "Pronto p/ Effecti",
    "disputa": "Em disputa",
}

def _dur_horas(inicio, fim):
    """Duração em horas entre dois ISO timestamps; None se faltar algum."""
    if not inicio or not fim:
        return None
    try:
        di = datetime.fromisoformat(inicio)
        df = datetime.fromisoformat(fim)
        return max(0.0, (df - di).total_seconds() / 3600.0)
    except Exception:
        return None


@router.get("/pregoes/{pid}/auditoria")
def auditoria_pregao(pid: int, user=Depends(usuario_logado)):
    a = _ler(AUDIT_PATH, AUDIT_VAZIO)
    etapas = []
    for e in a["etapas"]:
        if e["pregao_id"] != pid:
            continue
        item = dict(e)
        item["rotulo"] = ROTULO_ETAPA.get(e.get("etapa"), e.get("etapa"))
        item["duracao_horas"] = _dur_horas(e.get("inicio"), e.get("fim"))
        etapas.append(item)
    # ordena pela sequência do fluxo, e por início como desempate
    etapas.sort(key=lambda e: (ORDEM_ETAPAS.index(e["etapa"]) if e["etapa"] in ORDEM_ETAPAS else 99,
                               e.get("inicio") or ""))
    total = sum(e["duracao_horas"] for e in etapas if e["duracao_horas"] is not None)
    return {
        "etapas": etapas,
        "log": [l for l in a["log"] if l["pregao_id"] == pid],
        "total_horas": round(total, 2),
    }


# ordem canônica das etapas do fluxo (para exibir na ordem certa)
@router.get("/auditoria/resumo")
def auditoria_resumo(user=Depends(usuario_logado)):
    """
    Visão agregada da auditoria de tempo: por etapa do fluxo, quantas ocorrências
    concluídas, o tempo médio e o tempo total (em horas). Só considera etapas
    fechadas (com início e fim). Serve para achar gargalos.
    """
    a = _ler(AUDIT_PATH, AUDIT_VAZIO)
    acc = {}  # etapa -> {"n":.., "soma":..}
    abertas = {}  # etapa -> quantas ainda em andamento (sem fim)
    for e in a["etapas"]:
        et = e.get("etapa")
        d = _dur_horas(e.get("inicio"), e.get("fim"))
        if d is None:
            abertas[et] = abertas.get(et, 0) + 1
            continue
        s = acc.setdefault(et, {"n": 0, "soma": 0.0})
        s["n"] += 1
        s["soma"] += d
    etapas = []
    for et in ORDEM_ETAPAS:
        s = acc.get(et)
        if not s and not abertas.get(et):
            continue
        n = s["n"] if s else 0
        soma = s["soma"] if s else 0.0
        etapas.append({
            "etapa": et, "rotulo": ROTULO_ETAPA.get(et, et),
            "concluidas": n, "em_andamento": abertas.get(et, 0),
            "media_horas": round(soma / n, 2) if n else None,
            "total_horas": round(soma, 2),
        })
    # etapas fora da ordem canônica (defensivo)
    for et, s in acc.items():
        if et not in ORDEM_ETAPAS:
            etapas.append({
                "etapa": et, "rotulo": ROTULO_ETAPA.get(et, et),
                "concluidas": s["n"], "em_andamento": abertas.get(et, 0),
                "media_horas": round(s["soma"] / s["n"], 2) if s["n"] else None,
                "total_horas": round(s["soma"], 2),
            })
    return {"etapas": etapas}


@router.get("/auditoria/pregoes")
def auditoria_lista_pregoes(user=Depends(usuario_logado)):
    """Lista enxuta de pregões que têm trilha de tempo, para o seletor da
    visualização por pregão (número, órgão, status)."""
    a = _ler(AUDIT_PATH, AUDIT_VAZIO)
    pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
    com_trilha = {e["pregao_id"] for e in a["etapas"]}
    por_id = {p["id"]: p for p in pregoes}
    out = []
    for pid in com_trilha:
        p = por_id.get(pid)
        out.append({
            "id": pid,
            "orgao": (p or {}).get("orgao"), "numero": (p or {}).get("numero"),
            "status": (p or {}).get("status"),
        })
    out.sort(key=lambda x: (x.get("numero") or ""), reverse=True)
    return {"pregoes": out}


@router.get("/licoes")
def listar_licoes(user=Depends(usuario_logado)):
    """Lições aprendidas (declínios na triagem e na revisão). Enriquece cada
    registro com o status atual do pregão e devolve da mais recente à mais antiga."""
    licoes = _ler(LICOES_PATH, LICOES_VAZIO)
    pregoes = _ler(PREGOES_PATH, PREGOES_VAZIO)
    status_por_id = {p["id"]: p.get("status") for p in pregoes}
    out = []
    for l in licoes:
        item = dict(l)
        item["status_pregao"] = status_por_id.get(l.get("pregao_id"))
        out.append(item)
    out.sort(key=lambda x: (x.get("em") or ""), reverse=True)
    return {"licoes": out}


class AcompanhamentoDisputaIn(BaseModel):
    concorrente_vencedor: Optional[str] = None
    modelo_concorrente: Optional[str] = None
    valor_concorrente: Optional[float] = None

@router.patch("/licoes/{lid}/acompanhamento")
def registrar_acompanhamento_disputa(lid: int, dados: AcompanhamentoDisputaIn, user=Depends(usuario_logado)):
    """Registra o que o concorrente vencedor ofertou num item que a Safenet
    declinou na cotação — base de conhecimento pra próximas cotações, mesmo
    sem chance de ganhar esse item. Só coordenador e analista (que
    acompanham a disputa) podem preencher."""
    papel = user.get("papel")
    if papel not in ("coordenador", "analista", "analista_cotacao"):
        raise HTTPException(status_code=403,
            detail="Só coordenador e analista podem registrar o acompanhamento da disputa.")
    with _lock:
        licoes = _ler(LICOES_PATH, LICOES_VAZIO)
        l = next((x for x in licoes if x["id"] == lid), None)
        if not l:
            raise HTTPException(status_code=404, detail="Lição não encontrada")
        if l.get("origem") != "cotacao":
            raise HTTPException(status_code=400,
                detail="Acompanhamento da disputa só se aplica a declínios da cotação.")
        l["concorrente_vencedor"] = (dados.concorrente_vencedor or "").strip() or None
        l["modelo_concorrente"] = (dados.modelo_concorrente or "").strip() or None
        l["valor_concorrente"] = dados.valor_concorrente
        l["acompanhamento_por"] = user["nome"]
        l["acompanhamento_em"] = AGORA()
        _escrever_atomico(licoes, LICOES_PATH)
    log_acao(l.get("pregao_id"), user["nome"], "registrou_acompanhamento_disputa", {"licao_id": lid})
    return {"ok": True}


@router.get("/historico")
def listar_historico(user=Depends(usuario_logado)):
    """Histórico de itens encerrados (aprovados ou declinados). Base para a
    Cotação futura oferecer reaproveitamento de cotações anteriores."""
    return {"historico": _ler(HISTORICO_PATH, HISTORICO_VAZIO)}


@router.get("/health")
def health():
    return {"status": "ok", "servico": "intranet-safenet", "app": "licitacoes"}


# ---------------------------------------------------------------------------
# Inicia a thread de fundo do SLA diário — de propósito, é a ÚLTIMA linha do
# módulo: só roda depois que TUDO acima (PAPEL_DA_ETAPA, ETAPA_DE, rotas
# etc.) já terminou de carregar, eliminando qualquer risco de corrida entre
# a thread e o carregamento do módulo.
# ---------------------------------------------------------------------------
threading.Thread(target=_loop_sla_diario, daemon=True, name="sla-diario").start()
