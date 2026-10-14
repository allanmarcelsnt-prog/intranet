"""
rh_routes.py — Gestor de Recrutamento e Seleção · Safenet Tecnologia
---------------------------------------------------------------------
MAP-RH-001 · Fatia 9: Banco de talentos avançado + estatísticas (versão completa)

Registro no main.py — ACIMA do app.mount("/", StaticFiles(...)):
    from rh_routes import router as rh_router
    app.include_router(rh_router)

Padrão de persistência: idêntico ao licitacoes_routes.py
  _ler / _escrever_atomico / _proximo_id / _lock / AGORA
  BASE = Path(__file__).parent · data/ · escrita atômica · lock de thread

Autenticação: exige_papel_rh de auth_routes (campo papeis_extras).
Coordenadores da intranet (papel='coordenador') têm acesso total por herança.
"""
import json
import os
import re
import secrets
import smtplib
import urllib.request
import urllib.error
import ssl
import tempfile
import threading
import time
from datetime import datetime, date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from auth_routes import usuario_logado, exige_papel_rh
import auth_store

router = APIRouter(prefix="/api/rh", tags=["rh"])

# ---------------------------------------------------------------------------
# PATHS — mesmo padrão BASE = Path(__file__).parent do licitacoes_routes.py
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent
RH_VAGAS_PATH       = BASE / "data" / "rh_vagas.json"
RH_CARGOS_PATH      = BASE / "data" / "rh_cargos.json"   # Fatia 10: catálogo de cargos
DATASHEETS_RH       = BASE / "data" / "datasheets" / "rh"   # PDFs dos currículos
PDF_MAX_BYTES       = 10 * 1024 * 1024  # 10 MB
RH_CANDIDATOS_PATH  = BASE / "data" / "rh_candidatos.json"
RH_ONBOARDING_PATH  = BASE / "data" / "rh_onboarding.json"
RH_BANCO_PATH       = BASE / "data" / "rh_banco_talentos.json"
RH_AUDITORIA_PATH   = BASE / "data" / "rh_auditoria.json"
RH_EMAIL_LOG_PATH   = BASE / "data" / "rh_email_log.json"
RH_SLA_LOG_PATH     = BASE / "data" / "rh_sla_log.json"

_lock       = threading.Lock()
_email_lock = threading.Lock()
_sla_lock   = threading.Lock()

AGORA = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# PERSISTÊNCIA — idêntica ao licitacoes_routes.py
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
# AUDITORIA — mesma estrutura do licitacoes_routes.py
# ---------------------------------------------------------------------------
def _abrir_etapa(vaga_id, candidato_id, etapa, usuario, nota=None):
    a = _ler(RH_AUDITORIA_PATH, {"etapas": [], "log": []})
    a["etapas"].append({
        "id": _proximo_id(a["etapas"]),
        "vaga_id": vaga_id, "candidato_id": candidato_id,
        "etapa": etapa, "usuario": usuario,
        "inicio": AGORA(), "fim": None, "nota": nota,
    })
    _escrever_atomico(a, RH_AUDITORIA_PATH)


def _fechar_etapa(candidato_id, etapa):
    a = _ler(RH_AUDITORIA_PATH, {"etapas": [], "log": []})
    for e in reversed(a["etapas"]):
        if e.get("candidato_id") == candidato_id and e.get("etapa") == etapa and e.get("fim") is None:
            e["fim"] = AGORA()
            break
    _escrever_atomico(a, RH_AUDITORIA_PATH)


def _log_acao(vaga_id, usuario, acao, detalhe=None):
    a = _ler(RH_AUDITORIA_PATH, {"etapas": [], "log": []})
    a["log"].append({
        "id": _proximo_id(a["log"]),
        "vaga_id": vaga_id, "usuario": usuario,
        "acao": acao, "detalhe": detalhe, "em": AGORA(),
    })
    _escrever_atomico(a, RH_AUDITORIA_PATH)


# ---------------------------------------------------------------------------
# E-MAIL — mesmo padrão do licitacoes_routes.py
# ---------------------------------------------------------------------------
SMTP_HOST      = os.environ.get("INTRANET_SMTP_HOST", "").strip()
SMTP_PORT      = int(os.environ.get("INTRANET_SMTP_PORT", "465") or "465")
SMTP_USER      = os.environ.get("INTRANET_SMTP_USER", "").strip()
SMTP_PASS      = os.environ.get("INTRANET_SMTP_PASS", "")
SMTP_STARTTLS  = os.environ.get("INTRANET_SMTP_STARTTLS", "false").strip().lower() not in ("false", "0")
SMTP_FROM_NOME = "Gestor de RH — Safenet"
SMTP_FROM      = SMTP_USER
RH_BASE_URL    = os.environ.get("INTRANET_BASE_URL", "http://intranet.safenet.lan:8081/rh/").strip()


# ---------------------------------------------------------------------------
# TRIAGEM IA — integração com a API Anthropic (claude-sonnet-4-6)
# Princípio: a IA gera insumo (pontuação + parecer); a Rafaela valida e decide.
# A IA nunca reprova candidatos — ela pontua e justifica.
# Chamadas via urllib (sem dependência httpx/requests) via BackgroundTasks.
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_URL     = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL   = "claude-sonnet-4-6"


def _smtp_ok() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def _reg_email_log(destinatarios, assunto, evento, ok, erro=None):
    with _email_lock:
        log = _ler(RH_EMAIL_LOG_PATH, [])
        log.append({"em": AGORA(), "evento": evento, "destinatarios": destinatarios,
                    "assunto": assunto, "ok": ok, "erro": erro})
        if len(log) > 500:
            log = log[-500:]
        _escrever_atomico(log, RH_EMAIL_LOG_PATH)


def _html_email(titulo: str, corpo: str, cta_texto=None, cta_url=None) -> str:
    cta = ""
    if cta_texto and cta_url:
        cta = (f'<tr><td style="padding:22px 32px 6px">'
               f'<a href="{cta_url}" style="display:inline-block;background:#7AC143;color:#fff;'
               f'text-decoration:none;font-weight:700;font-size:14px;padding:11px 22px;'
               f'border-radius:8px;font-family:Arial,Helvetica,sans-serif">{cta_texto}</a>'
               f'</td></tr>')
    return (
        '<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:0;background:#f2f4f3;font-family:Arial,Helvetica,sans-serif">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f3;padding:28px 0">'
        '<tr><td align="center">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0"'
        ' style="background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e2e6e3;max-width:92vw">'
        '<tr><td style="background:#164023;padding:20px 32px">'
        '<span style="color:#fff;font-size:16px;font-weight:800;letter-spacing:.3px">RH</span>'
        '<span style="color:#bcd9c7;font-size:12px;margin-left:8px">Gestor de Recrutamento · Safenet</span>'
        '</td></tr>'
        f'<tr><td style="padding:28px 32px 4px">'
        f'<h1 style="margin:0 0 14px;font-size:18px;color:#164023;font-weight:800">{titulo}</h1>'
        f'<div style="font-size:14px;line-height:1.55;color:#333">{corpo}</div>'
        f'</td></tr>{cta}'
        '<tr><td style="padding:22px 32px 28px">'
        '<div style="border-top:1px solid #e2e6e3;padding-top:16px;font-size:11.5px;color:#8a9aab">'
        'Este e-mail foi gerado automaticamente pelo Gestor de RH. Não responda.'
        '</div></td></tr></table></td></tr></table></body></html>'
    )


def enviar_email(destinatarios, assunto: str, titulo: str, corpo: str, evento: str,
                 cta_texto=None, cta_url=None) -> bool:
    """Nunca levanta exceção pro chamador. Usar via BackgroundTasks nas rotas."""
    destinatarios = sorted({d.strip() for d in (destinatarios or []) if d and "@" in d})
    if not destinatarios:
        return False
    if not _smtp_ok():
        _reg_email_log(destinatarios, assunto, evento, False, "SMTP não configurado")
        return False
    html = _html_email(titulo, corpo, cta_texto, cta_url)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"]    = formataddr((SMTP_FROM_NOME, SMTP_FROM))
    msg["To"]      = ", ".join(destinatarios)
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        if SMTP_STARTTLS:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_FROM, destinatarios, msg.as_string())
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT,
                                  context=ssl.create_default_context(), timeout=15) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_FROM, destinatarios, msg.as_string())
        _reg_email_log(destinatarios, assunto, evento, True)
        return True
    except Exception as e:
        _reg_email_log(destinatarios, assunto, evento, False, str(e))
        return False


# ---------------------------------------------------------------------------
# HELPERS DE E-MAIL — destinatários por papel RH
# ---------------------------------------------------------------------------
def _emails_rh_coordenadores():
    """E-mails de todos os usuários com papel rh_coordenador ou coordenador."""
    todos = auth_store.listar_emails()
    out = []
    for u in todos:
        if not u.get("email"):
            continue
        if u.get("papel") == "coordenador":
            out.append(u["email"])
        elif "rh_coordenador" in (u.get("papeis_extras") or []):
            out.append(u["email"])
    return list(set(out))


def _emails_rh_operadores():
    todos = auth_store.listar_emails()
    return [u["email"] for u in todos
            if u.get("email") and "rh_operador" in (u.get("papeis_extras") or [])]


def _emails_pedro_e_rafaela() -> list:
    """Destinatários padrão das notificações: pmendes (Pedro) + rcerqueira (Rafaela C.)."""
    try:
        todos = auth_store.listar_admin_rh()
        return [u.get("email", "") for u in todos
                if u.get("usuario") in ("pmendes", "rcerqueira") and u.get("email")]
    except Exception:
        return []


def _email_solicitante_vaga(vaga: dict) -> list:
    """Retorna o e-mail do coordenador que abriu a vaga, buscando pelo nome do solicitante."""
    solicitante = (vaga.get("solicitante") or "").strip().lower()
    if not solicitante:
        return []
    try:
        todos = auth_store.listar_admin_rh()
        for u in todos:
            if u.get("nome", "").strip().lower() == solicitante and u.get("email"):
                return [u["email"]]
    except Exception:
        pass
    return []


def _sanitizar_nome(nome: str) -> str:
    """Remove caracteres especiais do nome para uso em path de arquivo."""
    return re.sub(r'[^a-zA-Z0-9_\-]', '_', nome)[:60]


def _salvar_pdf(vaga_id: int, candidato_id: int, nome: str, conteudo: bytes) -> str:
    """Salva o PDF do currículo em datasheets/rh/{vaga_id}/ e retorna o path relativo."""
    pasta = DATASHEETS_RH / str(vaga_id)
    pasta.mkdir(parents=True, exist_ok=True)
    nome_arquivo = f"{candidato_id}_{_sanitizar_nome(nome)}.pdf"
    caminho = pasta / nome_arquivo
    caminho.write_bytes(conteudo)
    return str(caminho.relative_to(BASE))


def _extrair_texto_pdf(conteudo: bytes) -> Optional[str]:
    """Extrai texto do PDF usando pdfplumber. Retorna None se falhar (PDF escaneado)."""
    try:
        import pdfplumber, io
        with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
            partes = []
            for pg in pdf.pages:
                t = pg.extract_text()
                if t:
                    partes.append(t.strip())
            texto = "\n\n".join(partes).strip()
            return texto if len(texto) > 20 else None
    except Exception:
        return None


def _chamar_anthropic(prompt_sistema: str, prompt_usuario: str,
                      max_tokens: int = 1000) -> Optional[str]:
    """Chama a API Anthropic e retorna o texto da resposta.
    Retorna None se a chave não estiver configurada ou se houver erro.
    Nunca levanta exceção pro chamador."""
    if not ANTHROPIC_API_KEY:
        return None
    corpo = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": prompt_sistema,
        "messages": [{"role": "user", "content": prompt_usuario}],
    }).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=corpo,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
            return dados["content"][0]["text"]
    except Exception:
        return None


def _montar_prompt_triagem(vaga: dict, curriculo_texto: str) -> tuple[str, str]:
    """Monta os prompts de sistema e usuário para a triagem de currículo."""
    req  = "\n".join(f"- {r}" for r in vaga.get("requisitos", []))   or "Não especificados."
    dif  = "\n".join(f"- {d}" for d in vaga.get("diferenciais", [])) or "Não especificados."
    cargo = vaga.get("cargo", "—")

    sistema = (
        "Você é um assistente especializado em triagem de currículos para RH. "
        "Analisa currículos com objetividade e retorna APENAS um JSON válido, "
        "sem texto extra, sem blocos de código markdown. "
        "Seu papel é gerar insumo para a decisão humana — você NUNCA decide pelo candidato."
    )

    usuario = f"""Analise o currículo abaixo para a vaga de **{cargo}**.

REQUISITOS ELIMINATÓRIOS DA VAGA:
{req}

DIFERENCIAIS VALORIZADOS:
{dif}

CURRÍCULO DO CANDIDATO:
{curriculo_texto[:4000]}

Retorne SOMENTE o seguinte JSON (sem markdown, sem texto antes ou depois):
{{
  "pontuacao": <número inteiro de 0 a 100>,
  "parecer": "<apto|atencao|inapto>",
  "justificativa": "<resumo objetivo em 2-3 frases>",
  "criterios": [
    {{
      "chave": "<identificador>",
      "rotulo": "<texto do requisito ou diferencial>",
      "tipo": "<requisito|diferencial>",
      "atende": <true|false|null>,
      "observacao": "<evidência no currículo ou null>"
    }}
  ]
}}

Regras:
- pontuacao 0-100: 0-39 = inapto, 40-69 = atenção, 70-100 = apto
- parecer deve ser coerente com a pontuação
- atende=null quando não é possível determinar pelo currículo
- criterios deve cobrir todos os requisitos e diferenciais listados
- Seja objetivo e baseie-se apenas no texto do currículo fornecido"""

    return sistema, usuario


def _executar_triagem_ia(candidato_id: int, vaga_id: int):
    """Executa a triagem IA em background e atualiza o candidato no JSON.
    Chamada via BackgroundTasks — nunca bloqueia a resposta HTTP."""
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    c = next((x for x in candidatos if x["id"] == candidato_id), None)
    if not c:
        return

    vagas = _ler(RH_VAGAS_PATH, [])
    vaga = next((v for v in vagas if v["id"] == vaga_id), None)
    if not vaga:
        return

    # Texto do currículo: prefere o extraído do PDF, fallback no colado
    curriculo_texto = (
        c.get("curriculo_texto_extraido") or
        c.get("curriculo_texto") or
        ""
    ).strip()

    if not curriculo_texto:
        # Sem texto — marca como não processado
        with _lock:
            candidatos2 = _ler(RH_CANDIDATOS_PATH, [])
            for x in candidatos2:
                if x["id"] == candidato_id:
                    x["parecer_ia"] = "sem_texto"
                    x["justificativa_ia"] = "Currículo sem texto extraível. Analise o PDF manualmente."
                    break
            _escrever_atomico(candidatos2, RH_CANDIDATOS_PATH)
        return

    sistema, usuario = _montar_prompt_triagem(vaga, curriculo_texto)
    resposta = _chamar_anthropic(sistema, usuario)

    if not resposta:
        with _lock:
            candidatos2 = _ler(RH_CANDIDATOS_PATH, [])
            for x in candidatos2:
                if x["id"] == candidato_id:
                    x["parecer_ia"] = "erro_api"
                    x["justificativa_ia"] = "Erro ao contatar a API de IA. Tente novamente."
                    break
            _escrever_atomico(candidatos2, RH_CANDIDATOS_PATH)
        return

    try:
        # limpar possível markdown residual
        texto = resposta.strip()
        if texto.startswith("```"):
            texto = texto.split("```")[1]
            if texto.startswith("json"):
                texto = texto[4:]
        dados_ia = json.loads(texto.strip())
    except (json.JSONDecodeError, IndexError):
        dados_ia = {
            "pontuacao": None, "parecer": "erro_parse",
            "justificativa": "Resposta da IA em formato inesperado.",
            "criterios": []
        }

    with _lock:
        candidatos2 = _ler(RH_CANDIDATOS_PATH, [])
        for x in candidatos2:
            if x["id"] == candidato_id:
                x["pontuacao_ia"]       = dados_ia.get("pontuacao")
                x["parecer_ia"]         = dados_ia.get("parecer")
                x["justificativa_ia"]   = dados_ia.get("justificativa")
                x["criterios_ia"]       = dados_ia.get("criterios", [])
                x["triagem_ia_em"]      = AGORA()
                break
        _escrever_atomico(candidatos2, RH_CANDIDATOS_PATH)

    _log_acao(vaga_id, "ia", "triagem_concluida", {
        "candidato_id": candidato_id,
        "pontuacao": dados_ia.get("pontuacao"),
        "parecer": dados_ia.get("parecer"),
    })


def _email_por_papel_rh(papel_rh: str):
    todos = auth_store.listar_emails()
    return [u["email"] for u in todos
            if u.get("email") and papel_rh in (u.get("papeis_extras") or [])]


# ---------------------------------------------------------------------------
# SLA — 1 dia útil por etapa (dias úteis = seg a sex)
# ---------------------------------------------------------------------------
SLA_DIAS_UTEIS = 1  # prazo por etapa

ETAPAS_STATUS = ["triagem", "selecao", "entrevista_social", "entrevista_tecnica", "aprovado"]
RESPONSAVEL_ETAPA = {
    "triagem":           "rh_operador",
    "selecao":           "rh_tecnico",
    "entrevista_social": "rh_operador",
    "entrevista_tecnica":"rh_tecnico",
    "aprovado":          "rh_coordenador",
}


def _dias_uteis_desde(data_str: str) -> Optional[int]:
    """Calcula dias úteis (seg–sex) entre data_str e hoje."""
    if not data_str:
        return None
    try:
        inicio = datetime.strptime(data_str, "%Y-%m-%d %H:%M:%S").date()
    except Exception:
        return None
    hoje = date.today()
    if inicio > hoje:
        return 0
    dias = 0
    d = inicio
    while d < hoje:
        d += timedelta(days=1)
        if d.weekday() < 5:  # seg=0 … sex=4
            dias += 1
    return dias


def _hoje_str():
    return date.today().isoformat()


def _ja_avisado_hoje(candidato_id: int, tipo: str) -> bool:
    log = _ler(RH_SLA_LOG_PATH, [])
    hoje = _hoje_str()
    return any(l.get("candidato_id") == candidato_id
               and l.get("tipo") == tipo
               and l.get("dia") == hoje for l in log)


def _marcar_avisado_hoje(candidato_id: int, tipo: str):
    with _sla_lock:
        log = _ler(RH_SLA_LOG_PATH, [])
        log.append({"candidato_id": candidato_id, "tipo": tipo,
                    "dia": _hoje_str(), "em": AGORA()})
        if len(log) > 3000:
            log = log[-3000:]
        _escrever_atomico(log, RH_SLA_LOG_PATH)


def _rodar_scan_sla():
    """Varre candidatos em etapas ativas E tarefas de onboarding.
    Dispara e-mails de aviso para responsável + coordenadores.
    Roda uma vez por dia — idempotente (não duplica avisos no mesmo dia)."""

    # ── 1. SLA de candidatos por etapa ────────────────────────────────
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    vagas = {v["id"]: v for v in _ler(RH_VAGAS_PATH, [])}
    coords = _emails_rh_coordenadores()

    for c in candidatos:
        status = c.get("status", "")
        if status not in ETAPAS_STATUS:
            continue
        cid   = c["id"]
        vid   = c.get("vaga_id")
        vaga  = vagas.get(vid, {})
        cargo = vaga.get("cargo", "—")
        nome  = c.get("nome", "—")
        entrada = c.get("data_entrada_etapa")
        du = _dias_uteis_desde(entrada)
        if du is None or du < SLA_DIAS_UTEIS:
            continue
        tipo = f"sla_{status}"
        if _ja_avisado_hoje(cid, tipo):
            continue

        papel_resp = RESPONSAVEL_ETAPA.get(status, "")
        emails_resp = _email_por_papel_rh(papel_resp) if papel_resp else []
        # Regra 8: SLA estourado → pmendes + rcerqueira
        destinatarios = list(set(_emails_pedro_e_rafaela()))
        if not destinatarios:
            _marcar_avisado_hoje(cid, tipo)
            continue

        rotulo = {"triagem": "Triagem", "selecao": "Seleção (Rodrigo)",
                  "entrevista_social": "Entrevista Social",
                  "entrevista_tecnica": "Entrevista Técnica",
                  "aprovado": "Aprovação Final"}.get(status, status)

        enviar_email(
            destinatarios=destinatarios,
            assunto=f"⏱ SLA estourado — {nome} · {cargo} ({rotulo})",
            titulo=f"Candidato parado há {du} dia(s) útil(eis)",
            corpo=(f"<p><b>{nome}</b> está na etapa <b>{rotulo}</b> "
                   f"da vaga <b>{cargo}</b> há <b>{du} dia(s) útil(eis)</b> sem movimentação.</p>"
                   f"<p>SLA definido: 1 dia útil por etapa.</p>"
                   f"<p style='color:#888;font-size:12px'>Este aviso é enviado diariamente enquanto a etapa permanecer parada.</p>"),
            evento="sla_candidato",
            cta_texto="Abrir o Kanban", cta_url=RH_BASE_URL,
        )
        _marcar_avisado_hoje(cid, tipo)

    # ── 2. SLA de tarefas de onboarding ───────────────────────────────
    onboardings = _ler(RH_ONBOARDING_PATH, [])
    for ob in onboardings:
        nome_colab = ob.get("nome", "—")
        cargo_ob   = ob.get("cargo", "—")
        admitido   = ob.get("admitido_em", "")

        for t in ob.get("tarefas", []):
            if t.get("concluida"):
                continue
            tid = t.get("id")
            if not admitido:
                continue
            try:
                dt_admissao = datetime.strptime(admitido, "%Y-%m-%d %H:%M:%S").date()
            except Exception:
                continue
            dia_previsto = dt_admissao + timedelta(days=t.get("dia", 999))
            du_tarefa = 0
            d = dia_previsto
            hoje_d = date.today()
            while d < hoje_d:
                d += timedelta(days=1)
                if d.weekday() < 5:
                    du_tarefa += 1
            if du_tarefa < SLA_DIAS_UTEIS:
                continue

            tipo_ob = f"sla_ob_{ob['id']}_{tid}"
            if _ja_avisado_hoje(ob["id"], tipo_ob):
                continue

            papel_t = t.get("responsavel", "")
            emails_t = _email_por_papel_rh(papel_t) if papel_t else []
            dest_ob = list(set(emails_t) | set(coords))
            if not dest_ob:
                _marcar_avisado_hoje(ob["id"], tipo_ob)
                continue

            enviar_email(
                destinatarios=dest_ob,
                assunto=f"⏱ Onboarding em atraso — {nome_colab} · {t.get('descricao','—')}",
                titulo="Tarefa de onboarding atrasada",
                corpo=(f"<p>A tarefa <b>{t.get('descricao','—')}</b> do onboarding de "
                       f"<b>{nome_colab}</b> ({cargo_ob}) está atrasada há {du_tarefa} dia(s) útil(eis).</p>"
                       f"<p>Prevista para o dia {t.get('dia','?')} após a admissão.</p>"
                       f"<p style='color:#888;font-size:12px'>Este aviso é enviado diariamente enquanto a tarefa estiver pendente.</p>"),
                evento="sla_onboarding",
                cta_texto="Abrir o Onboarding", cta_url=RH_BASE_URL,
            )
            _marcar_avisado_hoje(ob["id"], tipo_ob)

    # ── 3. Alerta de reajuste salarial (Fatia 10) ────────────────────────
    try:
        d_cat = _ler_cargos()
        proximo = d_cat.get("_meta", {}).get("proximo_reajuste", "")
        antecedencia = int(d_cat.get("_meta", {}).get("alerta_antecedencia_dias", 30))
        logins_alerta = d_cat.get("_meta", {}).get("alerta_destinatarios", [])
        if proximo and logins_alerta:
            dt_reajuste = datetime.strptime(proximo, "%Y-%m-%d").date()
            dias_restantes = (dt_reajuste - date.today()).days
            if 0 <= dias_restantes <= antecedencia:
                tipo_raj = "reajuste_salarial"
                if not _ja_avisado_hoje(0, tipo_raj):
                    emails_raj = []
                    try:
                        todos_u = auth_store.listar_admin_rh()
                        emails_raj = [u.get("email", "") for u in todos_u
                                      if u.get("usuario") in logins_alerta
                                      and u.get("email")]
                    except Exception:
                        pass
                    if emails_raj:
                        enviar_email(
                            destinatarios=emails_raj,
                            assunto=f"⚠ Reajuste salarial em {dias_restantes} dia(s) — Tabela Safenet",
                            titulo="Atenção: reajuste salarial próximo",
                            corpo=(
                                f"<p>A tabela salarial vigente expira em "
                                f"<b>{dt_reajuste.strftime('%d/%m/%Y')}</b> "
                                f"— faltam <b>{dias_restantes} dia(s)</b>.</p>"
                                "<p>Verifique com a diretoria os novos valores e atualize o catálogo "
                                "de cargos (<code>data/rh_cargos.json</code>) antes do vencimento.</p>"
                                "<p style='color:#888;font-size:12px'>Este aviso é enviado diariamente "
                                "até a data de reajuste.</p>"
                            ),
                            evento="alerta_reajuste",
                            cta_texto="Abrir o Gestor de RH", cta_url=RH_BASE_URL,
                        )
                    _marcar_avisado_hoje(0, tipo_raj)
    except Exception:
        pass  # nunca quebra a rotina de SLA por erro no alerta de reajuste


def _loop_sla_rh():
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


# ---------------------------------------------------------------------------
# STATUS / ETAPAS VÁLIDAS
# ---------------------------------------------------------------------------
STATUS_VALIDOS = [
    "triagem", "selecao", "entrevista_social",
    "entrevista_tecnica", "aprovado", "reprovado", "banco_talentos", "onboarding",
]

STATUS_ATIVOS = ["triagem", "selecao", "entrevista_social", "entrevista_tecnica", "aprovado"]

ROTULO_STATUS = {
    "triagem":            "Triagem",
    "selecao":            "Seleção",
    "entrevista_social":  "Ent. Social",
    "entrevista_tecnica": "Ent. Técnica",
    "aprovado":           "Aprovado",
    "reprovado":          "Reprovado",
    "banco_talentos":     "Banco de Talentos",
    "onboarding":         "Onboarding",
}


# ---------------------------------------------------------------------------
# MODELOS
# ---------------------------------------------------------------------------
class NovaVagaIn(BaseModel):
    cargo: str                        # nome do cargo (texto livre ou do catálogo)
    cargo_id: Optional[str] = None   # id do catálogo rh_cargos.json (Fatia 10)
    solicitante: str                  # nome do coordenador que abriu
    requisitos: list[str] = []        # requisitos eliminatórios
    diferenciais: list[str] = []      # diferenciais valorizados
    observacoes: Optional[str] = None


class EditarVagaIn(BaseModel):
    cargo: Optional[str] = None
    requisitos: Optional[list[str]] = None
    diferenciais: Optional[list[str]] = None
    observacoes: Optional[str] = None
    ativa: Optional[bool] = None


class NovoCandidatoIn(BaseModel):
    nome: str
    email: str
    telefone: str
    cidade: Optional[str] = None
    uf: Optional[str] = None
    origem: Optional[str] = None      # LinkedIn, RioVagas, Instagram, Indicação, Outro
    observacao: Optional[str] = None
    curriculo_texto: Optional[str] = None  # texto colado do currículo (Fatia 2); PDF na Fatia 4


class MoverCandidatoIn(BaseModel):
    status: str
    motivo: Optional[str] = None        # obrigatório ao reprovar
    nivel_aprovado: Optional[str] = None   # ex.: "A1", "A3" — preenchido ao aprovar cargo multi_nivel
    salario_nivel: Optional[float] = None  # valor do nível selecionado (calculado no front)


class OnboardingTarefaIn(BaseModel):
    concluida: bool


# ---------------------------------------------------------------------------
# DIAGNÓSTICO / STATUS
# ---------------------------------------------------------------------------
@router.get("/status")
def rh_status(user=Depends(usuario_logado)):
    papeis_rh = [p for p in user.get("papeis_extras", [])
                 if p in auth_store.PAPEIS_RH_VALIDOS]
    return {
        "status": "ok",
        "modulo": "Gestor de Recrutamento e Seleção",
        "versao": "1.3 — COMPLETO (todas as 9 fatias implantadas)",
        "usuario": user["nome"],
        "papel_principal": user["papel"],
        "papeis_rh": papeis_rh,
        "acesso_total_por_heranca": user.get("papel") == "coordenador",
    }


@router.get("/meu-acesso")
def meu_acesso(user=Depends(usuario_logado)):
    eh = user.get("papel") == "coordenador"
    ex = set(user.get("papeis_extras", []))
    return {
        "usuario": user["nome"],
        "papel_principal": user["papel"],
        "rh_coordenador": eh or "rh_coordenador" in ex,
        "rh_operador":    eh or bool(ex & {"rh_operador", "rh_coordenador"}),
        "rh_tecnico":     eh or bool(ex & {"rh_tecnico", "rh_coordenador"}),
        "rh_solicitante": eh or bool(ex & {"rh_solicitante","rh_operador","rh_tecnico","rh_coordenador"}),
        "papeis_rh": list(ex & set(auth_store.PAPEIS_RH_VALIDOS)),
    }


# ---------------------------------------------------------------------------
# VAGAS
# ---------------------------------------------------------------------------
@router.get("/vagas")
def listar_vagas(user=Depends(exige_papel_rh(
    ["rh_operador", "rh_coordenador", "rh_tecnico", "rh_solicitante"]
))):
    vagas = _ler(RH_VAGAS_PATH, [])
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    # enriquecer com contagem de candidatos por status
    for v in vagas:
        cands_vaga = [c for c in candidatos if c.get("vaga_id") == v["id"]]
        v["_total_candidatos"] = len(cands_vaga)
        v["_candidatos_ativos"] = sum(1 for c in cands_vaga if c.get("status") in STATUS_ATIVOS)
        v["_por_status"] = {}
        for s in STATUS_VALIDOS:
            n = sum(1 for c in cands_vaga if c.get("status") == s)
            if n:
                v["_por_status"][s] = n
    return {"vagas": vagas, "total": len(vagas)}


@router.post("/vagas")
def criar_vaga(dados: NovaVagaIn,
               user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador", "rh_solicitante"]))):
    cargo = (dados.cargo or "").strip()
    if not cargo:
        raise HTTPException(status_code=400, detail="Cargo é obrigatório.")

    # Fatia 10: enriquecer com dados do catálogo se cargo_id informado
    cargo_dados = None
    if dados.cargo_id:
        d_cat = _ler_cargos()
        cargo_cat = next((c for c in d_cat.get("cargos", [])
                          if c["id"] == dados.cargo_id), None)
        if cargo_cat:
            niveis = d_cat.get("niveis", {})
            bp = d_cat.get("beneficios_padrao", {}).get(cargo_cat.get("tipo","clt"), {})
            cargo_dados = {
                "id":           cargo_cat["id"],
                "nome":         cargo_cat["nome"],
                "tipo":         cargo_cat.get("tipo", "clt"),
                "multi_nivel":  cargo_cat.get("multi_nivel", False),
                "nivel_min":    cargo_cat.get("nivel_min"),
                "nivel_max":    cargo_cat.get("nivel_max"),
                "salario_min":  cargo_cat.get("salario_min") or niveis.get(cargo_cat.get("nivel_min","")),
                "salario_max":  cargo_cat.get("salario_max") or niveis.get(cargo_cat.get("nivel_max","")),
                "bolsa_fixa":   cargo_cat.get("bolsa_fixa"),
                "horario":      cargo_cat.get("horario", ""),
                "observacao":   cargo_cat.get("observacao", ""),
                "beneficios":   {**bp, **(cargo_cat.get("beneficios_override") or {})},
                "descricao_vaga":       cargo_cat.get("descricao_vaga", ""),
                "responsabilidades":    cargo_cat.get("responsabilidades", []),
                "requisitos_catalogo":  cargo_cat.get("requisitos", []),
                "diferenciais_catalogo":cargo_cat.get("diferenciais", []),
            }
            # usar nome do catálogo como cargo se o campo texto estava vazio
            if not dados.cargo.strip():
                cargo = cargo_cat["nome"]

    with _lock:
        vagas = _ler(RH_VAGAS_PATH, [])
        nova = {
            "id":           _proximo_id(vagas),
            "cargo":        cargo,
            "cargo_id":     dados.cargo_id or None,
            "cargo_dados":  cargo_dados,
            "solicitante":  (dados.solicitante or user["nome"]).strip(),
            "requisitos":   [r.strip() for r in (dados.requisitos or []) if r.strip()],
            "diferenciais": [d.strip() for d in (dados.diferenciais or []) if d.strip()],
            "observacoes":  (dados.observacoes or "").strip() or None,
            "ativa":        True,
            "criada_por":   user["nome"],
            "criada_em":    AGORA(),
            "token_publico": secrets.token_urlsafe(32),  # link público de candidatura
            "token_ativo":   True,   # ativo por padrão ao criar
            "anuncio_linkedin": None,
            "anuncio_riov":     None,
            "anuncio_instagram": None,
        }
        vagas.append(nova)
        _escrever_atomico(vagas, RH_VAGAS_PATH)
    _log_acao(nova["id"], user["nome"], "criou_vaga", {"cargo": cargo})
    return {"ok": True, "vaga": nova}


@router.get("/vagas/{vid}")
def obter_vaga(vid: int,
               user=Depends(exige_papel_rh(["rh_operador","rh_coordenador","rh_tecnico","rh_solicitante"]))):
    vagas = _ler(RH_VAGAS_PATH, [])
    v = next((v for v in vagas if v["id"] == vid), None)
    if not v:
        raise HTTPException(status_code=404, detail="Vaga não encontrada.")
    candidatos = [c for c in _ler(RH_CANDIDATOS_PATH, []) if c.get("vaga_id") == vid]
    return {"vaga": v, "candidatos": candidatos}


@router.patch("/vagas/{vid}")
def editar_vaga(vid: int, dados: EditarVagaIn,
                user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))):
    with _lock:
        vagas = _ler(RH_VAGAS_PATH, [])
        v = next((v for v in vagas if v["id"] == vid), None)
        if not v:
            raise HTTPException(status_code=404, detail="Vaga não encontrada.")
        if dados.cargo is not None:
            v["cargo"] = dados.cargo.strip()
        if dados.requisitos is not None:
            v["requisitos"] = [r.strip() for r in dados.requisitos if r.strip()]
        if dados.diferenciais is not None:
            v["diferenciais"] = [d.strip() for d in dados.diferenciais if d.strip()]
        if dados.observacoes is not None:
            v["observacoes"] = dados.observacoes.strip() or None
        if dados.ativa is not None:
            v["ativa"] = dados.ativa
        v["editada_por"] = user["nome"]
        v["editada_em"]  = AGORA()
        _escrever_atomico(vagas, RH_VAGAS_PATH)
    _log_acao(vid, user["nome"], "editou_vaga")
    return {"ok": True, "vaga": v}


# ---------------------------------------------------------------------------
# CANDIDATOS
# ---------------------------------------------------------------------------
@router.get("/vagas/{vid}/candidatos")
def listar_candidatos(vid: int,
                      user=Depends(exige_papel_rh(["rh_operador","rh_coordenador","rh_tecnico"]))):
    vagas = _ler(RH_VAGAS_PATH, [])
    if not any(v["id"] == vid for v in vagas):
        raise HTTPException(status_code=404, detail="Vaga não encontrada.")
    candidatos = [c for c in _ler(RH_CANDIDATOS_PATH, []) if c.get("vaga_id") == vid]
    # calcular SLA de cada candidato
    for c in candidatos:
        if c.get("status") in STATUS_ATIVOS:
            du = _dias_uteis_desde(c.get("data_entrada_etapa"))
            c["_dias_uteis_na_etapa"] = du
            c["_sla_estourado"] = du is not None and du >= SLA_DIAS_UTEIS
        else:
            c["_dias_uteis_na_etapa"] = None
            c["_sla_estourado"] = False
    return {"candidatos": candidatos, "total": len(candidatos)}


@router.post("/vagas/{vid}/candidatos")
def adicionar_candidato(vid: int, dados: NovoCandidatoIn, bt: BackgroundTasks,
                        user=Depends(exige_papel_rh(["rh_operador","rh_coordenador"]))):
    vagas = _ler(RH_VAGAS_PATH, [])
    vaga = next((v for v in vagas if v["id"] == vid), None)
    if not vaga:
        raise HTTPException(status_code=404, detail="Vaga não encontrada.")
    if not vaga.get("ativa", True):
        raise HTTPException(status_code=400, detail="Esta vaga está encerrada.")
    nome = (dados.nome or "").strip()
    email = (dados.email or "").strip().lower()
    if not nome or not email:
        raise HTTPException(status_code=400, detail="Nome e e-mail são obrigatórios.")
    with _lock:
        candidatos = _ler(RH_CANDIDATOS_PATH, [])
        # evita duplicata por e-mail na mesma vaga
        if any(c.get("email") == email and c.get("vaga_id") == vid for c in candidatos):
            raise HTTPException(status_code=409,
                detail="Candidato com este e-mail já cadastrado nesta vaga.")
        novo = {
            "id":                _proximo_id(candidatos),
            "vaga_id":           vid,
            "nome":              nome,
            "email":             email,
            "telefone":          (dados.telefone or "").strip(),
            "cidade":            (dados.cidade or "").strip() or None,
            "uf":                (dados.uf or "").strip() or None,
            "origem":            dados.origem or "Manual",
            "observacao":        (dados.observacao or "").strip() or None,
            "curriculo_texto":   dados.curriculo_texto or None,
            "curriculo_path":    None,    # Fatia 4 (upload PDF)
            "curriculo_texto_extraido": None,  # Fatia 4 (pdfplumber)
            "pontuacao_ia":      None,    # pontuação 0-100 gerada pela IA
            "parecer_ia":        None,    # parecer textual da IA
            "criterios_ia":      [],      # [{chave, rotulo, atende, obs}]
            "status":            "triagem",
            "motivo_reprovacao": None,
            "data_entrada_etapa": AGORA(),
            "sla_avisado_em":    None,
            "cadastrado_por":    user["nome"],
            "cadastrado_em":     AGORA(),
        }
        candidatos.append(novo)
        _escrever_atomico(candidatos, RH_CANDIDATOS_PATH)
    _abrir_etapa(vid, novo["id"], "triagem", user["nome"])
    _log_acao(vid, user["nome"], "adicionou_candidato",
              {"candidato_id": novo["id"], "nome": nome, "origem": novo["origem"]})
    # notificar Pedro e Rafaela (regra 2)
    dest = list(set(_emails_pedro_e_rafaela()))
    bt.add_task(
        enviar_email, dest,
        f"Novo candidato: {nome} — {vaga['cargo']}",
        f"Novo candidato recebido 🧑‍💼",
        (f"<p><b>{nome}</b> foi cadastrado como candidato para a vaga <b>{vaga['cargo']}</b>.</p>"
         f"<p>Origem: {novo['origem']} · E-mail: {email}</p>"),
        "novo_candidato",
        "Abrir o Kanban", RH_BASE_URL,
    )
    return {"ok": True, "candidato": novo}


@router.get("/vagas/{vid}/candidatos/{cid}")
def obter_candidato(vid: int, cid: int,
                    user=Depends(exige_papel_rh(["rh_operador","rh_coordenador","rh_tecnico"]))):
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    c = next((c for c in candidatos if c["id"] == cid and c.get("vaga_id") == vid), None)
    if not c:
        raise HTTPException(status_code=404, detail="Candidato não encontrado.")
    if c.get("status") in STATUS_ATIVOS:
        du = _dias_uteis_desde(c.get("data_entrada_etapa"))
        c["_dias_uteis_na_etapa"] = du
        c["_sla_estourado"] = du is not None and du >= SLA_DIAS_UTEIS
    return {"candidato": c}


@router.post("/vagas/{vid}/candidatos/{cid}/triar")
def disparar_triagem(vid: int, cid: int, bt: BackgroundTasks,
                     user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))):
    """Dispara a triagem IA em background.
    Retorna imediatamente — o resultado aparece quando a IA processar.
    A Rafaela recarrega o card para ver o parecer."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503,
            detail="ANTHROPIC_API_KEY não configurada. Configure a variável de ambiente e reinicie o container.")

    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    c = next((x for x in candidatos if x["id"] == cid and x.get("vaga_id") == vid), None)
    if not c:
        raise HTTPException(status_code=404, detail="Candidato não encontrado.")

    tem_texto = bool(
        c.get("curriculo_texto_extraido") or c.get("curriculo_texto")
    )
    if not tem_texto:
        raise HTTPException(status_code=400,
            detail="Candidato sem texto de currículo. Adicione o currículo antes de triar.")

    # Marcar como "em processamento" para o front mostrar spinner
    with _lock:
        candidatos2 = _ler(RH_CANDIDATOS_PATH, [])
        for x in candidatos2:
            if x["id"] == cid:
                x["parecer_ia"]      = "processando"
                x["pontuacao_ia"]    = None
                x["justificativa_ia"]= None
                x["criterios_ia"]    = []
                x["triagem_ia_em"]   = None
                break
        _escrever_atomico(candidatos2, RH_CANDIDATOS_PATH)

    _log_acao(vid, user["nome"], "disparou_triagem_ia", {"candidato_id": cid})
    bt.add_task(_executar_triagem_ia, cid, vid)
    # Fit analysis em background — verifica outras vagas abertas após a triagem
    bt.add_task(_executar_fit_analysis, vid, cid)
    return {"ok": True, "mensagem": "Triagem IA iniciada. Recarregue o candidato em alguns segundos."}


@router.get("/vagas/{vid}/candidatos/{cid}/triagem")
def obter_triagem(vid: int, cid: int,
                  user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador", "rh_tecnico"]))):
    """Retorna o resultado da triagem IA do candidato.
    O front faz polling nesta rota enquanto parecer_ia='processando'."""
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    c = next((x for x in candidatos if x["id"] == cid and x.get("vaga_id") == vid), None)
    if not c:
        raise HTTPException(status_code=404, detail="Candidato não encontrado.")
    return {
        "candidato_id":   cid,
        "pontuacao_ia":   c.get("pontuacao_ia"),
        "parecer_ia":     c.get("parecer_ia"),
        "justificativa_ia": c.get("justificativa_ia"),
        "criterios_ia":   c.get("criterios_ia", []),
        "triagem_ia_em":  c.get("triagem_ia_em"),
        "processando":    c.get("parecer_ia") == "processando",
        "tem_texto":      bool(c.get("curriculo_texto_extraido") or c.get("curriculo_texto")),
        "anthropic_configurado": bool(ANTHROPIC_API_KEY),
    }


@router.patch("/vagas/{vid}/candidatos/{cid}/validar-triagem")
def validar_triagem(vid: int, cid: int,
                    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))):
    """Rafaela valida o parecer da IA — registra na auditoria.
    Não muda o status do candidato — apenas confirma que a Rafaela revisou."""
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    c = next((x for x in candidatos if x["id"] == cid and x.get("vaga_id") == vid), None)
    if not c:
        raise HTTPException(status_code=404, detail="Candidato não encontrado.")
    if not c.get("parecer_ia") or c.get("parecer_ia") == "processando":
        raise HTTPException(status_code=400, detail="Triagem IA ainda não concluída.")
    with _lock:
        candidatos2 = _ler(RH_CANDIDATOS_PATH, [])
        for x in candidatos2:
            if x["id"] == cid:
                x["triagem_validada_por"] = user["nome"]
                x["triagem_validada_em"]  = AGORA()
                break
        _escrever_atomico(candidatos2, RH_CANDIDATOS_PATH)
    _log_acao(vid, user["nome"], "validou_triagem_ia", {"candidato_id": cid,
              "parecer_ia": c.get("parecer_ia"), "pontuacao_ia": c.get("pontuacao_ia")})
    return {"ok": True}


# ---------------------------------------------------------------------------
# FIT ANALYSIS — sugestão de redirecionamento (Fatia 10)
# ---------------------------------------------------------------------------
FIT_DIFERENCA_MINIMA = 15   # pontos acima da vaga atual para sugerir redirecionamento

def _executar_fit_analysis(vaga_id_atual: int, candidato_id: int):
    """Roda em background após a triagem principal.
    Compara o currículo do candidato contra todas as outras vagas abertas.
    Se outra vaga pontuar FIT_DIFERENCA_MINIMA pontos acima, salva sugestão.
    """
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    c = next((x for x in candidatos if x["id"] == candidato_id), None)
    if not c:
        return
    pontuacao_atual = c.get("pontuacao_ia")
    if pontuacao_atual is None:
        return   # triagem não concluída ou sem texto

    curriculo = (c.get("curriculo_texto_extraido") or c.get("curriculo_texto") or "").strip()
    if not curriculo:
        return

    vagas = _ler(RH_VAGAS_PATH, [])
    outras_vagas = [v for v in vagas
                    if v.get("ativa", True) and v["id"] != vaga_id_atual
                    and not v.get("token_ativo") is False]   # vagas abertas

    sugestoes = []
    for vaga in outras_vagas[:5]:   # máx 5 vagas para não sobrecarregar a API
        sistema, usuario = _montar_prompt_triagem(vaga, curriculo)
        resposta = _chamar_anthropic(sistema, usuario)
        if not resposta:
            continue
        try:
            texto = resposta.strip()
            if texto.startswith("```"):
                texto = texto.split("```")[1]
                if texto.startswith("json"): texto = texto[4:]
            dados_ia = json.loads(texto.strip())
            pontuacao_fit = dados_ia.get("pontuacao")
            if pontuacao_fit and pontuacao_fit - pontuacao_atual >= FIT_DIFERENCA_MINIMA:
                sugestoes.append({
                    "vaga_id":   vaga["id"],
                    "cargo":     vaga.get("cargo","—"),
                    "pontuacao": pontuacao_fit,
                    "diferenca": round(pontuacao_fit - pontuacao_atual, 1),
                    "ignorado":  False,
                    "analisado_em": AGORA(),
                })
        except Exception:
            continue

    if sugestoes:
        sugestoes.sort(key=lambda x: x["diferenca"], reverse=True)
        with _lock:
            candidatos2 = _ler(RH_CANDIDATOS_PATH, [])
            for x in candidatos2:
                if x["id"] == candidato_id:
                    x["fit_sugestoes"] = sugestoes
                    break
            _escrever_atomico(candidatos2, RH_CANDIDATOS_PATH)


@router.post("/vagas/{vid}/candidatos/{cid}/redirecionar")
def redirecionar_candidato(vid: int, cid: int, nova_vaga_id: int,
                           bt: BackgroundTasks,
                           user=Depends(exige_papel_rh(["rh_operador","rh_coordenador"]))):
    """Move o candidato para outra vaga (a de melhor fit).
    O candidato original vai para banco de talentos e uma cópia é criada na nova vaga.
    """
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    c = next((x for x in candidatos if x["id"] == cid and x.get("vaga_id") == vid), None)
    if not c:
        raise HTTPException(status_code=404, detail="Candidato não encontrado.")
    vagas = _ler(RH_VAGAS_PATH, [])
    nova_vaga = next((v for v in vagas if v["id"] == nova_vaga_id), None)
    if not nova_vaga or not nova_vaga.get("ativa", True):
        raise HTTPException(status_code=404, detail="Vaga de destino não encontrada ou inativa.")

    with _lock:
        candidatos2 = _ler(RH_CANDIDATOS_PATH, [])
        # Mover original para banco de talentos
        for x in candidatos2:
            if x["id"] == cid:
                _fechar_etapa(cid, x.get("status"))
                x["status"] = "banco_talentos"
                x["data_entrada_etapa"] = AGORA()
                x["motivo_reprovacao"] = f"Redirecionado para vaga: {nova_vaga.get('cargo','—')} por {user['nome']}"
                break

        # Criar cópia na nova vaga
        novo_id = _proximo_id(candidatos2)
        nova_candidatura = {
            **{k: v for k, v in c.items()
               if k not in ("id","vaga_id","status","data_entrada_etapa",
                            "fit_sugestoes","motivo_reprovacao","nivel_aprovado","salario_nivel")},
            "id":                 novo_id,
            "vaga_id":            nova_vaga_id,
            "status":             "triagem",
            "data_entrada_etapa": AGORA(),
            "redirecionado_de":   vid,
            "redirecionado_por":  user["nome"],
            "fit_sugestoes":      None,
            "pontuacao_ia":       None,   # será reprocessado
            "parecer_ia":         None,
            "triagem_ia_em":      None,
        }
        candidatos2.append(nova_candidatura)
        _escrever_atomico(candidatos2, RH_CANDIDATOS_PATH)

    _abrir_etapa(nova_vaga_id, novo_id, "triagem", user["nome"],
                 f"Redirecionado da vaga {vid}")
    _log_acao(vid, user["nome"], "redirecionou_candidato",
              {"candidato_id": cid, "nova_vaga_id": nova_vaga_id,
               "novo_candidato_id": novo_id})

    # Disparar nova triagem IA na vaga de destino
    bt.add_task(_executar_triagem_ia, nova_vaga_id, novo_id)

    return {"ok": True, "novo_candidato_id": novo_id,
            "mensagem": f"{c['nome']} redirecionado para '{nova_vaga.get('cargo','—')}'."}


@router.patch("/vagas/{vid}/candidatos/{cid}/ignorar-fit")
def ignorar_sugestao_fit(vid: int, cid: int, vaga_fit_id: int,
                         user=Depends(exige_papel_rh(["rh_operador","rh_coordenador"]))):
    """Marca uma sugestão de redirecionamento como ignorada."""
    with _lock:
        candidatos = _ler(RH_CANDIDATOS_PATH, [])
        c = next((x for x in candidatos if x["id"] == cid and x.get("vaga_id") == vid), None)
        if not c:
            raise HTTPException(status_code=404, detail="Candidato não encontrado.")
        for s in (c.get("fit_sugestoes") or []):
            if s.get("vaga_id") == vaga_fit_id:
                s["ignorado"] = True
                s["ignorado_por"] = user["nome"]
                s["ignorado_em"]  = AGORA()
                break
        _escrever_atomico(candidatos, RH_CANDIDATOS_PATH)
    return {"ok": True}


@router.patch("/vagas/{vid}/candidatos/{cid}/status")
def mover_candidato(vid: int, cid: int, dados: MoverCandidatoIn, bt: BackgroundTasks,
                    user=Depends(exige_papel_rh(["rh_operador","rh_coordenador","rh_tecnico"]))):
    novo_status = dados.status
    if novo_status not in STATUS_VALIDOS:
        raise HTTPException(status_code=400,
            detail=f"Status inválido. Válidos: {STATUS_VALIDOS}")
    if novo_status == "reprovado" and not (dados.motivo or "").strip():
        raise HTTPException(status_code=400,
            detail="Motivo é obrigatório ao reprovar um candidato.")

    with _lock:
        candidatos = _ler(RH_CANDIDATOS_PATH, [])
        c = next((c for c in candidatos if c["id"] == cid and c.get("vaga_id") == vid), None)
        if not c:
            raise HTTPException(status_code=404, detail="Candidato não encontrado.")
        vagas = _ler(RH_VAGAS_PATH, [])
        vaga = next((v for v in vagas if v["id"] == vid), {})
        status_anterior = c.get("status")

        # ── Reprovação inteligente (Fatia 10) ────────────────────────────
        # Triagem → Reprovado (IA não qualificou, sem fit)
        # Seleção, Ent.Social, Ent.Técnica → Banco de Talentos (passou análise humana)
        if novo_status == "reprovado" and status_anterior in ("selecao","entrevista_social","entrevista_tecnica"):
            novo_status = "banco_talentos"

        _fechar_etapa(cid, status_anterior)
        c["status"] = novo_status
        c["data_entrada_etapa"] = AGORA()
        c["sla_avisado_em"] = None
        if dados.motivo:
            c["motivo_reprovacao"] = dados.motivo.strip()
        if novo_status == "aprovado" and dados.nivel_aprovado:
            c["nivel_aprovado"]  = dados.nivel_aprovado
            c["salario_nivel"]   = dados.salario_nivel
        if novo_status == "banco_talentos":
            banco = _ler(RH_BANCO_PATH, [])
            if not any(b.get("candidato_id") == cid for b in banco):
                banco.append({
                    "id":           _proximo_id(banco),
                    "candidato_id": cid,
                    "vaga_id":      vid,
                    "nome":         c["nome"],
                    "email":        c["email"],
                    "telefone":     c.get("telefone"),
                    "cargo_origem": vaga.get("cargo"),
                    "pontuacao_ia": c.get("pontuacao_ia"),
                    "curriculo_texto": c.get("curriculo_texto"),
                    "adicionado_em": AGORA(),
                    "motivo":       dados.motivo or "Avançado da etapa: " + (status_anterior or "—"),
                })
                _escrever_atomico(banco, RH_BANCO_PATH)
        _escrever_atomico(candidatos, RH_CANDIDATOS_PATH)

    if novo_status in STATUS_ATIVOS:
        _abrir_etapa(vid, cid, novo_status, user["nome"], dados.motivo)
    _log_acao(vid, user["nome"], "moveu_candidato",
              {"candidato_id": cid, "de": status_anterior, "para": novo_status,
               "motivo": dados.motivo})
    # e-mails de transição
    cargo = vaga.get("cargo", "—") if "vaga" in dir() else "—"
    base_dest = list(set(_emails_pedro_e_rafaela()))
    coord_solicitante = _email_solicitante_vaga(vaga) if "vaga" in dir() else []

    if novo_status == "selecao":
        # Regra 4: pmendes + rcerqueira + coordenador solicitante
        dest = list(set(base_dest) | set(coord_solicitante))
        bt.add_task(enviar_email, dest,
            f"Candidatos prontos para seleção — {cargo}",
            "Triagem concluída ✅",
            f"<p><b>{c['nome']}</b> avançou para a etapa de <b>Seleção</b> na vaga <b>{cargo}</b>.</p>"
            f"<p>Revise o currículo e avance ou reprove o candidato no Kanban.</p>",
            "triagem_concluida", "Abrir o Kanban", RH_BASE_URL)
    elif novo_status == "entrevista_social":
        # Regra 5: pmendes + rcerqueira
        dest = base_dest
        bt.add_task(enviar_email, dest,
            f"Entrevista social solicitada — {c['nome']}",
            "Candidato selecionado para entrevista social 📋",
            f"<p><b>{c['nome']}</b> foi selecionado para a entrevista social. Vaga: <b>{cargo}</b>.</p>"
            f"<p>A Rafaela Cerqueira realizará a entrevista e registrará o resultado no sistema.</p>",
            "selecao_concluida", "Abrir o Kanban", RH_BASE_URL)
    elif novo_status == "entrevista_tecnica":
        # Regra 6: pmendes + rcerqueira + coordenador solicitante
        dest = list(set(base_dest) | set(coord_solicitante))
        bt.add_task(enviar_email, dest,
            f"Candidato para entrevista técnica — {c['nome']}",
            "Entrevista social concluída — candidato aprovado 🤝",
            f"<p><b>{c['nome']}</b> foi aprovado na entrevista social. Próximo: <b>entrevista técnica</b>. Vaga: <b>{cargo}</b>.</p>"
            f"<p>A Rafaela agendará a entrevista presencial. O coordenador responsável conduzirá e registrará o resultado e o nível de contratação no Kanban.</p>",
            "social_concluida", "Abrir o Kanban", RH_BASE_URL)
    elif novo_status == "aprovado":
        # Regra 7: pmendes + rcerqueira
        nivel_txt = f" — Nível: {dados.nivel_aprovado} (R$ {dados.salario_nivel:,.2f})".replace(",","X").replace(".",",").replace("X",".") if dados.nivel_aprovado else ""
        dest = base_dest
        bt.add_task(enviar_email, dest,
            f"Candidato aprovado — {c['nome']} · {cargo}",
            "Candidato aprovado para contratação 🎉",
            f"<p><b>{c['nome']}</b> foi aprovado para a vaga <b>{cargo}</b>{nivel_txt}.</p>"
            f"<p>Próximo passo: admissão e início do onboarding de 30 dias.</p>",
            "candidato_aprovado", "Abrir o Kanban", RH_BASE_URL)
    return {"ok": True, "status": novo_status}


# ---------------------------------------------------------------------------
# CATÁLOGO DE CARGOS (Fatia 10)
# ---------------------------------------------------------------------------
def _ler_cargos() -> dict:
    """Carrega rh_cargos.json; retorna estrutura vazia se ainda não existe."""
    if not RH_CARGOS_PATH.exists():
        return {"niveis": {}, "cargos": [], "beneficios_padrao": {}, "_meta": {}}
    with RH_CARGOS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/cargos")
def listar_cargos(user=Depends(exige_papel_rh(
    ["rh_operador", "rh_coordenador", "rh_solicitante", "rh_tecnico"]
))):
    """Retorna o catálogo de cargos com faixas salariais e benefícios."""
    d = _ler_cargos()
    niveis = d.get("niveis", {})
    bp_padrao = d.get("beneficios_padrao", {})
    cargos_out = []
    for c in d.get("cargos", []):
        ec = dict(c)
        bp = bp_padrao.get(c.get("tipo", "clt"), {})
        ec["beneficios"] = {**bp, **(c.get("beneficios_override") or {})}
        if not ec.get("salario_min") and c.get("nivel_min"):
            ec["salario_min"] = niveis.get(c["nivel_min"])
        if not ec.get("salario_max") and c.get("nivel_max"):
            ec["salario_max"] = niveis.get(c["nivel_max"])
        cargos_out.append(ec)
    return {"cargos": cargos_out, "niveis": niveis, "meta": d.get("_meta", {})}


@router.get("/cargos/{cargo_id}")
def obter_cargo(cargo_id: str, user=Depends(exige_papel_rh(
    ["rh_operador", "rh_coordenador", "rh_solicitante", "rh_tecnico"]
))):
    """Retorna os dados completos de um cargo específico do catálogo."""
    d = _ler_cargos()
    niveis = d.get("niveis", {})
    cargo = next((c for c in d.get("cargos", []) if c["id"] == cargo_id), None)
    if not cargo:
        raise HTTPException(status_code=404, detail="Cargo não encontrado no catálogo.")
    bp = d.get("beneficios_padrao", {}).get(cargo.get("tipo", "clt"), {})
    ec = dict(cargo)
    ec["beneficios"] = {**bp, **(cargo.get("beneficios_override") or {})}
    if not ec.get("salario_min") and cargo.get("nivel_min"):
        ec["salario_min"] = niveis.get(cargo["nivel_min"])
    if not ec.get("salario_max") and cargo.get("nivel_max"):
        ec["salario_max"] = niveis.get(cargo["nivel_max"])
    return {"cargo": ec, "niveis": niveis}


# ---------------------------------------------------------------------------
# KANBAN — visão agregada (uma chamada, todas as vagas)
# ---------------------------------------------------------------------------
@router.get("/kanban")
def kanban(user=Depends(exige_papel_rh(
    ["rh_operador","rh_coordenador","rh_tecnico","rh_solicitante"]
))):
    """Retorna vagas + candidatos organizados por status — alimenta o Kanban do front."""
    vagas = _ler(RH_VAGAS_PATH, [])
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    # só vagas ativas
    vagas_ativas = [v for v in vagas if v.get("ativa", True)]
    out = []
    for v in vagas_ativas:
        cands = [c for c in candidatos if c.get("vaga_id") == v["id"]]
        por_status = {}
        for s in STATUS_VALIDOS:
            lista = [c for c in cands if c.get("status") == s]
            for c in lista:
                du = _dias_uteis_desde(c.get("data_entrada_etapa"))
                c["_sla_estourado"] = (du is not None and du >= SLA_DIAS_UTEIS
                                       and s in STATUS_ATIVOS)
                c["_dias_uteis_na_etapa"] = du
            por_status[s] = lista
        out.append({**v, "candidatos_por_status": por_status,
                    "total_candidatos": len(cands)})
    return {"vagas": out, "colunas": STATUS_VALIDOS, "rotulos": ROTULO_STATUS}


# ---------------------------------------------------------------------------
# BANCO DE TALENTOS
# ---------------------------------------------------------------------------
@router.get("/banco-talentos")
def listar_banco(
    cargo:         Optional[str] = None,
    pontuacao_min: Optional[int] = None,
    origem:        Optional[str] = None,
    user=Depends(exige_papel_rh(
        ["rh_operador","rh_coordenador","rh_tecnico","rh_solicitante"]
    ))):
    """Lista o banco de talentos com filtros avançados."""
    banco = _ler(RH_BANCO_PATH, [])
    if cargo:
        banco = [b for b in banco
                 if cargo.lower() in (b.get("cargo_origem") or "").lower()]
    if pontuacao_min is not None:
        banco = [b for b in banco
                 if (b.get("pontuacao_ia") or 0) >= pontuacao_min]
    if origem:
        banco = [b for b in banco
                 if origem.lower() in (b.get("origem") or "").lower()]
    # ordenar por pontuação IA decrescente
    banco = sorted(banco, key=lambda b: b.get("pontuacao_ia") or 0, reverse=True)
    return {"banco": banco, "total": len(banco)}


@router.get("/banco-talentos/{bid}")
def obter_banco(bid: int,
                user=Depends(exige_papel_rh(
                    ["rh_operador","rh_coordenador","rh_tecnico"]))):
    banco = _ler(RH_BANCO_PATH, [])
    b = next((b for b in banco if b["id"] == bid), None)
    if not b:
        raise HTTPException(status_code=404, detail="Candidato não encontrado no banco.")
    return {"candidato": b}


@router.patch("/banco-talentos/{bid}/nota")
def adicionar_nota_banco(bid: int, nota: str,
                         user=Depends(exige_papel_rh(["rh_operador","rh_coordenador"]))):
    """Adiciona uma observação ao candidato no banco de talentos."""
    with _lock:
        banco = _ler(RH_BANCO_PATH, [])
        b = next((b for b in banco if b["id"] == bid), None)
        if not b:
            raise HTTPException(status_code=404, detail="Candidato não encontrado.")
        b["nota"] = nota.strip() or None
        b["nota_por"] = user["nome"]
        b["nota_em"]  = AGORA()
        _escrever_atomico(banco, RH_BANCO_PATH)
    return {"ok": True}


@router.delete("/banco-talentos/{bid}")
def remover_banco(bid: int,
                  user=Depends(exige_papel_rh(["rh_coordenador"]))):
    """Remove candidato do banco de talentos (apenas coordenador)."""
    with _lock:
        banco = _ler(RH_BANCO_PATH, [])
        antes = len(banco)
        banco = [b for b in banco if b["id"] != bid]
        if len(banco) == antes:
            raise HTTPException(status_code=404, detail="Candidato não encontrado.")
        _escrever_atomico(banco, RH_BANCO_PATH)
    return {"ok": True}


@router.post("/banco-talentos/{bid}/reativar")
def reativar_candidato(bid: int, vaga_id: int, bt: BackgroundTasks,
                       user=Depends(exige_papel_rh(["rh_operador","rh_coordenador"]))):
    """Reativa candidato do banco para uma nova vaga — sem preencher formulário de novo.
    Cria card de candidato na nova vaga com os dados já existentes."""
    banco = _ler(RH_BANCO_PATH, [])
    b = next((b for b in banco if b["id"] == bid), None)
    if not b:
        raise HTTPException(status_code=404, detail="Candidato não encontrado no banco.")

    vagas = _ler(RH_VAGAS_PATH, [])
    vaga = next((v for v in vagas if v["id"] == vaga_id), None)
    if not vaga:
        raise HTTPException(status_code=404, detail="Vaga não encontrada.")
    if not vaga.get("ativa", True):
        raise HTTPException(status_code=400, detail="A vaga está encerrada.")

    # verificar se já está nessa vaga
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    if any(c.get("email") == b.get("email") and c.get("vaga_id") == vaga_id
           for c in candidatos):
        raise HTTPException(status_code=409,
            detail="Candidato já está cadastrado nessa vaga.")

    with _lock:
        candidatos2 = _ler(RH_CANDIDATOS_PATH, [])
        novo = {
            "id":                   _proximo_id(candidatos2),
            "vaga_id":              vaga_id,
            "nome":                 b["nome"],
            "email":                b.get("email",""),
            "telefone":             b.get("telefone",""),
            "cidade":               b.get("cidade"),
            "uf":                   b.get("uf"),
            "origem":               "Banco de Talentos",
            "observacao":           b.get("nota"),
            "curriculo_texto":      b.get("curriculo_texto"),
            "curriculo_path":       None,
            "curriculo_texto_extraido": b.get("curriculo_texto"),
            "pontuacao_ia":         b.get("pontuacao_ia"),   # aproveita pontuação anterior
            "parecer_ia":           b.get("parecer_ia"),
            "justificativa_ia":     b.get("justificativa_ia"),
            "criterios_ia":         b.get("criterios_ia", []),
            "status":               "triagem",
            "motivo_reprovacao":    None,
            "data_entrada_etapa":   AGORA(),
            "sla_avisado_em":       None,
            "cadastrado_por":       user["nome"],
            "cadastrado_em":        AGORA(),
            "reativado_de_banco":   bid,
        }
        candidatos2.append(novo)
        _escrever_atomico(candidatos2, RH_CANDIDATOS_PATH)

    _abrir_etapa(vaga_id, novo["id"], "triagem", user["nome"],
                 f"Reativado do banco de talentos (bid={bid})")
    _log_acao(vaga_id, user["nome"], "reativou_do_banco",
              {"candidato_id": novo["id"], "nome": b["nome"], "bid": bid})

    # notificar Pedro e Rafaela (regra 3)
    dest = list(set(_emails_pedro_e_rafaela()))
    bt.add_task(
        enviar_email, dest,
        f"Candidato reativado do banco — {b['nome']} · {vaga.get('cargo','—')}",
        "Candidato do banco de talentos reativado 🔄",
        (f"<p><b>{b['nome']}</b> foi reativado do banco de talentos para a vaga "
         f"<b>{vaga.get('cargo','—')}</b> por {user['nome']}.</p>"
         f"{'<p>Pontuação IA anterior: '+str(b.get('pontuacao_ia'))+'pts</p>' if b.get('pontuacao_ia') else ''}"),
        "candidato_reativado",
        "Abrir o Kanban", RH_BASE_URL,
    )
    return {"ok": True, "candidato": novo}


# ---------------------------------------------------------------------------
# ESTATÍSTICAS — relatório final do processo de recrutamento
# ---------------------------------------------------------------------------
@router.get("/estatisticas")
def estatisticas(user=Depends(exige_papel_rh(
    ["rh_coordenador","rh_operador","rh_solicitante"]
))):
    """Relatório consolidado do processo de recrutamento.
    Métricas: tempo médio por etapa, taxa de aprovação, origem dos candidatos."""
    from collections import Counter

    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    vagas      = _ler(RH_VAGAS_PATH, [])
    banco      = _ler(RH_BANCO_PATH, [])
    onboards   = _ler_onboardings()

    # ── Vagas ──
    vagas_ativas    = [v for v in vagas if v.get("ativa", True)]
    vagas_encerradas = [v for v in vagas if not v.get("ativa", True)]

    # ── Candidatos por status ──
    por_status = Counter(c.get("status","") for c in candidatos)

    # ── Taxa de aprovação ──
    total_processados = sum(por_status.get(s,0)
                            for s in ["aprovado","reprovado","banco_talentos","onboarding"])
    aprovados = por_status.get("aprovado",0) + por_status.get("onboarding",0)
    taxa_aprovacao = round(aprovados / total_processados * 100, 1) if total_processados else 0

    # ── Origem dos candidatos ──
    origens = Counter(c.get("origem","Desconhecida") for c in candidatos)

    # ── Pontuação IA ──
    pontuacoes = [c.get("pontuacao_ia") for c in candidatos if c.get("pontuacao_ia") is not None]
    media_ia = round(sum(pontuacoes) / len(pontuacoes), 1) if pontuacoes else None

    # ── Onboarding ──
    ob_ativos     = [o for o in onboards if not o.get("encerrado_em")]
    ob_encerrados = [o for o in onboards if o.get("encerrado_em")]

    return {
        "vagas": {
            "total":     len(vagas),
            "ativas":    len(vagas_ativas),
            "encerradas":len(vagas_encerradas),
        },
        "candidatos": {
            "total":           len(candidatos),
            "por_status":      dict(por_status),
            "taxa_aprovacao":  taxa_aprovacao,
            "em_processo":     sum(por_status.get(s,0)
                                   for s in ["triagem","selecao",
                                             "entrevista_social","entrevista_tecnica"]),
        },
        "origem_candidatos": dict(origens),
        "triagem_ia": {
            "candidatos_triados": len(pontuacoes),
            "media_pontuacao":    media_ia,
            "aprovados_pela_ia":  sum(1 for c in candidatos
                                     if c.get("parecer_ia")=="apto"),
            "atencao_pela_ia":    sum(1 for c in candidatos
                                     if c.get("parecer_ia")=="atencao"),
            "inaptos_pela_ia":    sum(1 for c in candidatos
                                     if c.get("parecer_ia")=="inapto"),
        },
        "banco_talentos": {
            "total": len(banco),
            "por_cargo": dict(Counter(b.get("cargo_origem","—") for b in banco)),
        },
        "onboarding": {
            "ativos":    len(ob_ativos),
            "encerrados":len(ob_encerrados),
            "total":     len(onboards),
        },
        "gerado_em": AGORA(),
    }


# ---------------------------------------------------------------------------
# ONBOARDING
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# TRILHAS DE ONBOARDING POR CARGO
# Armazenadas em rh_onboarding.json sob a chave "trilhas_cargo".
# Editáveis pelo Pedro (rh_coordenador) via API.
# Usadas como base ao criar um onboarding — fallback para a trilha padrão.
# ---------------------------------------------------------------------------

TRILHA_PADRAO = [
    {"dia": 1,  "descricao": "Entrega de EPI e uniforme",         "responsavel": "rh_operador"},
    {"dia": 1,  "descricao": "Apresentação à equipe",             "responsavel": "rh_tecnico"},
    {"dia": 1,  "descricao": "Acesso aos sistemas internos (TI)", "responsavel": "rh_coordenador"},
    {"dia": 3,  "descricao": "Leitura do manual de conduta",      "responsavel": "rh_operador"},
    {"dia": 5,  "descricao": "Visita às instalações e clientes",  "responsavel": "rh_tecnico"},
    {"dia": 8,  "descricao": "Acompanhamento em campo (shadow)",  "responsavel": "rh_tecnico"},
    {"dia": 10, "descricao": "Treinamento NR10 interno",          "responsavel": "rh_tecnico"},
    {"dia": 12, "descricao": "Check-in de adaptação (RH)",        "responsavel": "rh_operador"},
    {"dia": 20, "descricao": "1ª avaliação de desempenho",        "responsavel": "rh_tecnico"},
    {"dia": 28, "descricao": "Feedback de adaptação (30 dias)",   "responsavel": "rh_operador"},
    {"dia": 30, "descricao": "Confirmação de efetivação",         "responsavel": "rh_coordenador"},
]

TRILHAS_POR_CARGO: dict = {
    "Técnico de Segurança Eletrônica": TRILHA_PADRAO,
    "Supervisor Técnico": [
        {"dia": 1,  "descricao": "Entrega de EPI e uniforme",                "responsavel": "rh_operador"},
        {"dia": 1,  "descricao": "Apresentação à equipe e clientes-chave",   "responsavel": "rh_coordenador"},
        {"dia": 1,  "descricao": "Acesso aos sistemas internos",              "responsavel": "rh_coordenador"},
        {"dia": 3,  "descricao": "Leitura do manual de conduta e políticas", "responsavel": "rh_operador"},
        {"dia": 5,  "descricao": "Alinhamento com a equipe técnica",         "responsavel": "rh_tecnico"},
        {"dia": 8,  "descricao": "Acompanhamento operacional em campo",      "responsavel": "rh_tecnico"},
        {"dia": 10, "descricao": "Treinamentos NR10 e NR35",                 "responsavel": "rh_tecnico"},
        {"dia": 15, "descricao": "Check-in de integração (RH)",              "responsavel": "rh_operador"},
        {"dia": 20, "descricao": "Apresentação do plano de metas",           "responsavel": "rh_coordenador"},
        {"dia": 28, "descricao": "Feedback de adaptação (30 dias)",          "responsavel": "rh_operador"},
        {"dia": 30, "descricao": "Confirmação de efetivação",                "responsavel": "rh_coordenador"},
    ],
    "Operador de Monitoramento": [
        {"dia": 1,  "descricao": "Entrega de EPI e uniforme",                "responsavel": "rh_operador"},
        {"dia": 1,  "descricao": "Apresentação à equipe e turno",            "responsavel": "rh_tecnico"},
        {"dia": 1,  "descricao": "Acesso aos sistemas de monitoramento",     "responsavel": "rh_coordenador"},
        {"dia": 2,  "descricao": "Treinamento no software de monitoramento", "responsavel": "rh_tecnico"},
        {"dia": 3,  "descricao": "Leitura dos procedimentos operacionais",   "responsavel": "rh_operador"},
        {"dia": 5,  "descricao": "Primeiro turno acompanhado",               "responsavel": "rh_tecnico"},
        {"dia": 10, "descricao": "Avaliação de desempenho inicial",          "responsavel": "rh_tecnico"},
        {"dia": 12, "descricao": "Check-in de adaptação ao turno (RH)",      "responsavel": "rh_operador"},
        {"dia": 20, "descricao": "Avaliação de competências técnicas",       "responsavel": "rh_tecnico"},
        {"dia": 28, "descricao": "Feedback de adaptação (30 dias)",          "responsavel": "rh_operador"},
        {"dia": 30, "descricao": "Confirmação de efetivação",                "responsavel": "rh_coordenador"},
    ],
    "Auxiliar Técnico": TRILHA_PADRAO,
}


def _obter_trilha_cargo(cargo: str) -> list:
    """Retorna a trilha personalizada para o cargo ou a padrão."""
    # Primeiro verifica trilhas customizadas salvas no JSON
    dados = _ler(RH_ONBOARDING_PATH, {})
    if isinstance(dados, dict):
        trilhas_custom = dados.get("trilhas_cargo", {})
        if cargo in trilhas_custom:
            return trilhas_custom[cargo]
    # Fallback: trilhas embutidas
    for chave, trilha in TRILHAS_POR_CARGO.items():
        if chave.lower() in cargo.lower() or cargo.lower() in chave.lower():
            return trilha
    return TRILHA_PADRAO


def _ler_onboardings() -> list:
    """Lê apenas a lista de onboardings (compatível com formato antigo lista e novo dict)."""
    dados = _ler(RH_ONBOARDING_PATH, [])
    if isinstance(dados, list):
        return dados
    return dados.get("onboardings", [])


def _escrever_onboardings(onboardings: list):
    """Escreve onboardings mantendo trilhas_cargo intactas."""
    dados = _ler(RH_ONBOARDING_PATH, {})
    if isinstance(dados, list):
        # migrar formato antigo (lista) para novo (dict)
        dados = {"onboardings": dados, "trilhas_cargo": {}}
    dados["onboardings"] = onboardings
    _escrever_atomico(dados, RH_ONBOARDING_PATH)


class NovaTarefaIn(BaseModel):
    descricao: str
    dia: int
    responsavel: str

class TrilhaCargoIn(BaseModel):
    cargo: str
    tarefas: list


@router.get("/onboarding")
def listar_onboardings(user=Depends(exige_papel_rh(
    ["rh_operador", "rh_coordenador", "rh_tecnico"]
))):
    obs = _ler_onboardings()
    for ob in obs:
        tarefas = ob.get("tarefas", [])
        total = len(tarefas)
        done  = sum(1 for t in tarefas if t.get("concluida"))
        ob["_progresso_pct"]      = round(done / total * 100) if total else 0
        ob["_tarefas_total"]      = total
        ob["_tarefas_concluidas"] = done
        ob["_tarefas_pendentes"]  = total - done
        ob["_encerrado"]          = ob.get("encerrado_em") is not None
    return {"onboardings": obs, "total": len(obs)}


@router.get("/onboarding/{oid}")
def obter_onboarding(oid: int,
                     user=Depends(exige_papel_rh(["rh_operador","rh_coordenador","rh_tecnico"]))):
    obs = _ler_onboardings()
    ob = next((o for o in obs if o["id"] == oid), None)
    if not ob:
        raise HTTPException(status_code=404, detail="Onboarding não encontrado.")
    tarefas = ob.get("tarefas", [])
    total = len(tarefas)
    done  = sum(1 for t in tarefas if t.get("concluida"))
    ob["_progresso_pct"]      = round(done / total * 100) if total else 0
    ob["_tarefas_total"]      = total
    ob["_tarefas_concluidas"] = done
    ob["_proximas_tarefas"]   = [t for t in tarefas if not t.get("concluida")][:3]
    ob["_encerrado"]          = ob.get("encerrado_em") is not None
    return {"onboarding": ob}


@router.post("/onboarding")
def criar_onboarding(candidato_id: int, vaga_id: int,
                     user=Depends(exige_papel_rh(["rh_operador","rh_coordenador"]))):
    """Ativa a trilha de onboarding de 30 dias — usa trilha por cargo se existir."""
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    c = next((c for c in candidatos if c["id"] == candidato_id), None)
    if not c:
        raise HTTPException(status_code=404, detail="Candidato não encontrado.")
    vagas = _ler(RH_VAGAS_PATH, [])
    vaga  = next((v for v in vagas if v["id"] == vaga_id), None)
    cargo = (vaga or {}).get("cargo", "—")
    trilha_base = _obter_trilha_cargo(cargo)
    with _lock:
        onboardings = _ler_onboardings()
        if any(o.get("candidato_id") == candidato_id for o in onboardings):
            raise HTTPException(status_code=409,
                detail="Onboarding já existe para este candidato.")
        novo = {
            "id":           _proximo_id(onboardings),
            "candidato_id": candidato_id,
            "vaga_id":      vaga_id,
            "nome":         c["nome"],
            "cargo":        cargo,
            "admitido_em":  AGORA(),
            "criado_por":   user["nome"],
            "encerrado_em": None,
            "encerrado_por":None,
            "observacao_encerramento": None,
            "tarefas": [
                {"id": i+1, "dia": t["dia"], "descricao": t["descricao"],
                 "responsavel": t["responsavel"],
                 "concluida": False, "concluida_em": None, "concluida_por": None}
                for i, t in enumerate(trilha_base)
            ],
        }
        onboardings.append(novo)
        _escrever_onboardings(onboardings)
    with _lock:
        cands2 = _ler(RH_CANDIDATOS_PATH, [])
        for x in cands2:
            if x["id"] == candidato_id:
                x["status"] = "onboarding"
                x["data_entrada_etapa"] = AGORA()
                break
        _escrever_atomico(cands2, RH_CANDIDATOS_PATH)
    _log_acao(vaga_id, user["nome"], "criou_onboarding",
              {"candidato_id": candidato_id, "nome": c["nome"], "cargo": cargo})
    return {"ok": True, "onboarding": novo}


@router.patch("/onboarding/{oid}/tarefas/{tid}")
def concluir_tarefa(oid: int, tid: int, dados: OnboardingTarefaIn,
                    user=Depends(exige_papel_rh(["rh_operador","rh_coordenador","rh_tecnico"]))):
    with _lock:
        onboardings = _ler_onboardings()
        ob = next((o for o in onboardings if o["id"] == oid), None)
        if not ob:
            raise HTTPException(status_code=404, detail="Onboarding não encontrado.")
        if ob.get("encerrado_em"):
            raise HTTPException(status_code=400, detail="Onboarding já encerrado.")
        t = next((t for t in ob["tarefas"] if t["id"] == tid), None)
        if not t:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        t["concluida"]    = dados.concluida
        t["concluida_em"] = AGORA() if dados.concluida else None
        t["concluida_por"]= user["nome"] if dados.concluida else None
        _escrever_onboardings(onboardings)
    return {"ok": True, "tarefa": t}


@router.post("/onboarding/{oid}/tarefas")
def adicionar_tarefa(oid: int, dados: NovaTarefaIn,
                     user=Depends(exige_papel_rh(["rh_coordenador"]))):
    with _lock:
        onboardings = _ler_onboardings()
        ob = next((o for o in onboardings if o["id"] == oid), None)
        if not ob:
            raise HTTPException(status_code=404, detail="Onboarding não encontrado.")
        if ob.get("encerrado_em"):
            raise HTTPException(status_code=400, detail="Onboarding já encerrado.")
        descricao = (dados.descricao or "").strip()
        if not descricao:
            raise HTTPException(status_code=400, detail="Descrição é obrigatória.")
        novo_id = max((t["id"] for t in ob["tarefas"]), default=0) + 1
        nova = {"id": novo_id, "dia": dados.dia, "descricao": descricao,
                "responsavel": dados.responsavel or "rh_operador",
                "concluida": False, "concluida_em": None, "concluida_por": None}
        ob["tarefas"].append(nova)
        ob["tarefas"].sort(key=lambda t: t["dia"])
        _escrever_onboardings(onboardings)
    _log_acao(ob.get("vaga_id",0), user["nome"], "adicionou_tarefa_onboarding",
              {"onboarding_id": oid, "descricao": descricao})
    return {"ok": True, "tarefa": nova}


@router.delete("/onboarding/{oid}/tarefas/{tid}")
def remover_tarefa(oid: int, tid: int,
                   user=Depends(exige_papel_rh(["rh_coordenador"]))):
    with _lock:
        onboardings = _ler_onboardings()
        ob = next((o for o in onboardings if o["id"] == oid), None)
        if not ob:
            raise HTTPException(status_code=404, detail="Onboarding não encontrado.")
        antes = len(ob["tarefas"])
        ob["tarefas"] = [t for t in ob["tarefas"] if t["id"] != tid]
        if len(ob["tarefas"]) == antes:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        _escrever_onboardings(onboardings)
    return {"ok": True}


@router.post("/onboarding/{oid}/encerrar")
def encerrar_onboarding(oid: int, bt: BackgroundTasks, observacao: str = "",
                        user=Depends(exige_papel_rh(["rh_coordenador"]))):
    with _lock:
        onboardings = _ler_onboardings()
        ob = next((o for o in onboardings if o["id"] == oid), None)
        if not ob:
            raise HTTPException(status_code=404, detail="Onboarding não encontrado.")
        if ob.get("encerrado_em"):
            raise HTTPException(status_code=400, detail="Onboarding já encerrado.")
        ob["encerrado_em"]  = AGORA()
        ob["encerrado_por"] = user["nome"]
        ob["observacao_encerramento"] = observacao.strip() or None
        _escrever_onboardings(onboardings)
    nome = ob.get("nome","—"); cargo = ob.get("cargo","—")
    _log_acao(ob.get("vaga_id",0), user["nome"], "encerrou_onboarding",
              {"onboarding_id": oid, "nome": nome})
    dest = list(set(_emails_rh_coordenadores()) | set(_emails_rh_operadores()))
    bt.add_task(enviar_email, dest,
        f"✅ Onboarding concluído — {nome} ({cargo})",
        "Colaborador efetivado!",
        (f"<p>O onboarding de <b>{nome}</b> ({cargo}) foi encerrado por <b>{user['nome']}</b>.</p>"
         f"{f'<p>Observação: {observacao}</p>' if observacao else ''}"),
        "onboarding_encerrado", "Abrir o Onboarding", RH_BASE_URL)
    return {"ok": True, "encerrado_em": ob["encerrado_em"]}


@router.get("/onboarding-trilhas")
def listar_trilhas(user=Depends(exige_papel_rh(["rh_coordenador","rh_operador"]))):
    dados = _ler(RH_ONBOARDING_PATH, {})
    trilhas_custom = dados.get("trilhas_cargo", {}) if isinstance(dados, dict) else {}
    todas = {}
    for cargo, trilha in TRILHAS_POR_CARGO.items():
        todas[cargo] = {"cargo": cargo, "tarefas": trilha, "customizada": False}
    for cargo, trilha in trilhas_custom.items():
        todas[cargo] = {"cargo": cargo, "tarefas": trilha, "customizada": True}
    return {"trilhas": list(todas.values())}


@router.put("/onboarding-trilhas")
def salvar_trilha_cargo(dados: TrilhaCargoIn,
                        user=Depends(exige_papel_rh(["rh_coordenador"]))):
    cargo = (dados.cargo or "").strip()
    if not cargo:
        raise HTTPException(status_code=400, detail="Cargo é obrigatório.")
    if not dados.tarefas:
        raise HTTPException(status_code=400, detail="A trilha deve ter ao menos uma tarefa.")
    for t in dados.tarefas:
        if not isinstance(t, dict) or not t.get("descricao") or not isinstance(t.get("dia"), int):
            raise HTTPException(status_code=400,
                detail="Cada tarefa deve ter 'descricao' e 'dia' (número).")
    with _lock:
        raw = _ler(RH_ONBOARDING_PATH, {})
        if isinstance(raw, list):
            raw = {"onboardings": raw, "trilhas_cargo": {}}
        raw.setdefault("trilhas_cargo", {})[cargo] = [
            {"dia": int(t.get("dia",1)),
             "descricao": str(t.get("descricao","")).strip(),
             "responsavel": str(t.get("responsavel","rh_operador"))}
            for t in dados.tarefas
        ]
        _escrever_atomico(raw, RH_ONBOARDING_PATH)
    _log_acao(0, user["nome"], "salvou_trilha_cargo", {"cargo": cargo})
    return {"ok": True, "cargo": cargo, "tarefas": len(dados.tarefas)}



# ---------------------------------------------------------------------------
# ADMIN — e-mail e SLA
# ---------------------------------------------------------------------------
@router.get("/admin/email/log")
def email_log(user=Depends(exige_papel_rh(["rh_coordenador"]))):
    """Retorna os últimos 200 registros do log de e-mail."""
    log = _ler(RH_EMAIL_LOG_PATH, [])
    return {
        "log": list(reversed(log[-200:])),
        "total": len(log),
        "smtp_configurado": _smtp_ok(),
    }


@router.post("/admin/sla/rodar-agora")
def sla_rodar_agora(user=Depends(exige_papel_rh(["rh_coordenador"]))):
    """Força a verificação de SLA imediatamente (sem esperar o ciclo diário)."""
    _rodar_scan_sla()
    return {"ok": True, "mensagem": "Scan de SLA executado."}


@router.get("/admin/sla/relatorio")
def sla_relatorio(user=Depends(exige_papel_rh(["rh_coordenador", "rh_operador"]))):
    """Relatório de SLA: candidatos atrasados por etapa + tarefas de onboarding atrasadas."""
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    vagas_dict = {v["id"]: v for v in _ler(RH_VAGAS_PATH, [])}
    onboardings = _ler(RH_ONBOARDING_PATH, [])

    atrasados = []
    for c in candidatos:
        status = c.get("status", "")
        if status not in ETAPAS_STATUS:
            continue
        du = _dias_uteis_desde(c.get("data_entrada_etapa"))
        if du is None or du < SLA_DIAS_UTEIS:
            continue
        vaga = vagas_dict.get(c.get("vaga_id"), {})
        rotulo = {"triagem":"Triagem","selecao":"Seleção","entrevista_social":"Ent. Social",
                  "entrevista_tecnica":"Ent. Técnica","aprovado":"Aprovação"}.get(status, status)
        atrasados.append({
            "tipo":         "candidato",
            "candidato_id": c["id"],
            "nome":         c.get("nome"),
            "cargo":        vaga.get("cargo","—"),
            "etapa":        rotulo,
            "dias_atraso":  du,
            "responsavel":  RESPONSAVEL_ETAPA.get(status,"—"),
        })

    ob_atrasados = []
    for ob in onboardings:
        admitido = ob.get("admitido_em","")
        if not admitido:
            continue
        try:
            dt_adm = datetime.strptime(admitido, "%Y-%m-%d %H:%M:%S").date()
        except Exception:
            continue
        for t in ob.get("tarefas",[]):
            if t.get("concluida"):
                continue
            dia_prev = dt_adm + timedelta(days=t.get("dia",999))
            du_t = 0
            d = dia_prev
            hj = date.today()
            while d < hj:
                d += timedelta(days=1)
                if d.weekday() < 5:
                    du_t += 1
            if du_t < SLA_DIAS_UTEIS:
                continue
            ob_atrasados.append({
                "tipo":         "onboarding",
                "colaborador":  ob.get("nome"),
                "cargo":        ob.get("cargo"),
                "tarefa":       t.get("descricao"),
                "dia_previsto": t.get("dia"),
                "dias_atraso":  du_t,
                "responsavel":  t.get("responsavel"),
            })

    return {
        "candidatos_atrasados":  atrasados,
        "onboarding_atrasado":   ob_atrasados,
        "total_atrasados":       len(atrasados) + len(ob_atrasados),
        "sla_dias_uteis":        SLA_DIAS_UTEIS,
        "gerado_em":             AGORA(),
    }


@router.post("/admin/sla/rodar-agora")
def sla_rodar_agora(user=Depends(exige_papel_rh(["rh_coordenador"]))):
    _rodar_scan_sla()
    return {"ok": True}


@router.get("/admin/equipe-rh")
def equipe_rh(user=Depends(exige_papel_rh(["rh_coordenador"]))):
    todos = auth_store.listar_admin_rh()
    equipe = []
    for u in todos:
        if not u.get("ativo", True):
            continue
        rh = [p for p in u.get("papeis_extras", []) if p in auth_store.PAPEIS_RH_VALIDOS]
        coord = u.get("papel") == "coordenador"
        if rh or coord:
            equipe.append({
                "id": u["id"], "nome": u["nome"], "usuario": u["usuario"],
                "papel_principal": u["papel"], "papeis_rh": rh,
                "acesso_total_por_heranca": coord,
                "email": u.get("email"), "tem_senha": u.get("tem_senha", False),
            })
    return {"equipe": equipe}


# ---------------------------------------------------------------------------
# FORMULÁRIO PÚBLICO DE CANDIDATURA
# Rotas sem autenticação JWT — acessíveis pelo Cloudflare Tunnel.
# Prefixo /candidatura/{token} (fora do /api/rh/).
# O router usa prefix="/api/rh" então estas rotas ficam em /api/candidatura/.
# O main.py precisa também servir o HTML em GET /candidatura/{token}
# via StaticFiles ou rota dedicada — resolvido abaixo com HTMLResponse.
# ---------------------------------------------------------------------------

# Router separado para rotas públicas (sem prefixo /api/rh)
public_router = APIRouter(tags=["candidatura-publica"])


def _normalizar(s: str) -> str:
    """Remove acentos, pontuação e converte para minúsculas para comparação."""
    import unicodedata
    sem_acento = ''.join(c for c in unicodedata.normalize('NFD', s.lower())
                         if unicodedata.category(c) != 'Mn')
    return ''.join(c if c.isalnum() or c == ' ' else ' ' for c in sem_acento).strip()


def _enriquecer_cargo_catalogo(cargo_cat: dict, d_cat: dict) -> dict:
    """Monta o mesmo formato de cargo_dados a partir de um cargo do catálogo."""
    niveis = d_cat.get("niveis", {})
    bp = d_cat.get("beneficios_padrao", {}).get(cargo_cat.get("tipo", "clt"), {})
    return {
        "tipo":          cargo_cat.get("tipo", "clt"),
        "multi_nivel":   cargo_cat.get("multi_nivel", False),
        "nivel_min":     cargo_cat.get("nivel_min"),
        "nivel_max":     cargo_cat.get("nivel_max"),
        "salario_min":   cargo_cat.get("salario_min") or niveis.get(cargo_cat.get("nivel_min", "")),
        "salario_max":   cargo_cat.get("salario_max") or niveis.get(cargo_cat.get("nivel_max", "")),
        "bolsa_fixa":    cargo_cat.get("bolsa_fixa"),
        "horario":       cargo_cat.get("horario", ""),
        "observacao":    cargo_cat.get("observacao", ""),
        "beneficios":    {**bp, **(cargo_cat.get("beneficios_override") or {})},
        "descricao_vaga":        cargo_cat.get("descricao_vaga", ""),
        "responsabilidades":     cargo_cat.get("responsabilidades", []),
        "requisitos_catalogo":   cargo_cat.get("requisitos", []),
        "diferenciais_catalogo": cargo_cat.get("diferenciais", []),
    }


def _buscar_cargo_no_catalogo(vaga: dict) -> dict:
    """Busca dados do catálogo para vagas que não têm cargo_dados salvo.
    Estratégias (em ordem): cargo_id → nome exato → palavras-chave (primeiros 5 chars).
    Permite que vagas criadas antes da Fatia 10 também exibam salário e benefícios.
    """
    try:
        d_cat = _ler_cargos()
        cargos_cat = d_cat.get("cargos", [])

        # 1. Por cargo_id salvo na vaga
        cargo_id = vaga.get("cargo_id")
        if cargo_id:
            c = next((x for x in cargos_cat if x.get("id") == cargo_id), None)
            if c:
                return _enriquecer_cargo_catalogo(c, d_cat)

        # 2. Por nome exato (case-insensitive)
        cargo_nome = _normalizar(vaga.get("cargo") or "")
        if not cargo_nome:
            return {}

        c = next((x for x in cargos_cat
                  if _normalizar(x.get("nome", "")) == cargo_nome), None)
        if c:
            return _enriquecer_cargo_catalogo(c, d_cat)

        # 3. Por palavras-chave (prefixo de 5 chars) — tolerante a plural/acento
        palavras = [p for p in cargo_nome.split() if len(p) >= 5]
        if not palavras:
            return {}

        melhor, melhor_score = None, 0
        for x in cargos_cat:
            cat_norm = _normalizar(x.get("nome", ""))
            cat_palavras = cat_norm.split()
            score = sum(
                1 for p in palavras
                if any(w.startswith(p[:5]) for w in cat_palavras)
            )
            if score > melhor_score:
                melhor_score, melhor = score, x

        if melhor and melhor_score >= max(1, len(palavras) // 2):
            return _enriquecer_cargo_catalogo(melhor, d_cat)
    except Exception:
        pass
    return {}


def _html_formulario(vaga: dict, token: str, erro: str = None, sucesso: bool = False) -> str:
    """Renderiza o HTML do formulário público de candidatura."""
    cargo    = vaga.get("cargo", "—")
    req_html = "".join(f"<li>{r}</li>" for r in vaga.get("requisitos", []))
    dif_html = "".join(f"<li>{d}</li>" for d in vaga.get("diferenciais", []))
    erro_html = f'<div class="erro">{erro}</div>' if erro else ""

    # Fatia 10: extrair dados do catálogo quando disponíveis
    cd = vaga.get("cargo_dados") or {}

    # Fallback: busca automática no catálogo para vagas criadas antes da Fatia 10
    if not cd:
        cd = _buscar_cargo_no_catalogo(vaga)
    tipo = cd.get("tipo", "clt")

    # Remuneração
    remuneracao_html = ""
    if tipo == "estagio" and cd.get("bolsa_fixa"):
        v = cd["bolsa_fixa"]
        remuneracao_html = f'<span class="vaga-tag">💰 Bolsa: R$ {v:,.2f}'.replace(",","X").replace(".",",").replace("X",".") + "</span>"
    elif cd.get("salario_min") and cd.get("salario_max"):
        mn = f'R$ {cd["salario_min"]:,.2f}'.replace(",","X").replace(".",",").replace("X",".")
        mx = f'R$ {cd["salario_max"]:,.2f}'.replace(",","X").replace(".",",").replace("X",".")
        sufixo = " (nível definido após avaliação técnica)" if cd.get("multi_nivel") else ""
        remuneracao_html = f'<span class="vaga-tag">💰 {mn} a {mx}{sufixo}</span>'

    # Benefícios
    beneficios_html = ""
    ben = cd.get("beneficios", {})
    ben_partes = []
    if ben.get("vale_transporte"):   ben_partes.append("VT")
    if ben.get("vale_alimentacao"):  ben_partes.append(f"VA ({ben['vale_alimentacao']})")
    if ben.get("seguro_vida"):       ben_partes.append("Seguro de Vida")
    if ben.get("sesc"):              ben_partes.append("Convênio SESC")
    if ben_partes:
        beneficios_html = f'<span class="vaga-tag">🎁 {" · ".join(ben_partes)}</span>'

    # Horário
    horario_html = ""
    if cd.get("horario"):
        horario_html = f'<span class="vaga-tag">🕐 {cd["horario"]}</span>'
    if cd.get("observacao"):
        horario_html += f'<span class="vaga-tag">⚠ {cd["observacao"]}</span>'

    # Bloco de tags de remuneração + benefícios
    tags_bloco = ""
    tags = remuneracao_html + beneficios_html + horario_html
    if tags:
        tags_bloco = f'<div class="vaga-tags">{tags}</div>'

    # Descrição do cargo
    desc_bloco = ""
    desc = cd.get("descricao_vaga", "")
    if desc:
        desc_bloco = f'<div class="vaga-desc">{desc}</div>'

    # Meta linha: tipo de vínculo + regime
    TIPO_LABEL = {"clt": "CLT · Tempo integral", "pj": "PJ · Prestador de serviços",
                  "estagio": "Estágio"}
    meta_label = f"Rio de Janeiro · {TIPO_LABEL.get(tipo, 'CLT · Tempo integral')}"
    erro_html = f'<div class="erro">{erro}</div>' if erro else ""
    if sucesso:
        return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Candidatura enviada — Safenet</title>
<style>{_CSS_FORM}</style></head><body>
<div class="shell">
  <div class="header" style="gap:14px">
  <img src="/logo.png" alt="Safenet" width="40" height="40" style="flex-shrink:0;display:block">
  <div class="header-text"><div class="h-brand">Safenet Tecnologia</div>
  <div class="h-sub">Gestão de Talentos</div></div></div>
  <div class="success-box">
    <div class="success-ico">✓</div>
    <h2>Candidatura enviada!</h2>
    <p>Seu currículo foi recebido com sucesso.<br>
    Nossa equipe de RH entrará em contato pelo e-mail informado.<br><br>
    <span style="font-size:13px;color:#6b6b68">Você pode fechar esta página.</span></p>
  </div>
</div></body></html>"""

    # Montar descrição para og:description
    desc_preview = cd.get("descricao_vaga", "") or f"Candidate-se à vaga de {cargo} na Safenet Tecnologia — Rio de Janeiro."
    if len(desc_preview) > 200:
        desc_preview = desc_preview[:197] + "..."
    og_url = f"https://vagas.safenettecnologia.com.br/candidatura/{token}"

    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vaga: {cargo} | Safenet Tecnologia</title>
<!-- Open Graph — preview no LinkedIn, WhatsApp, Instagram -->
<meta property="og:type"        content="website">
<meta property="og:url"         content="{og_url}">
<meta property="og:title"       content="🔎 Vaga: {cargo} | Safenet Tecnologia">
<meta property="og:description" content="{desc_preview}">
<meta property="og:site_name"   content="Safenet Tecnologia — Gestão de Talentos">
<meta property="og:image"       content="https://vagas.safenettecnologia.com.br/og-image.png">
<meta name="twitter:card"       content="summary">
<meta name="twitter:title"      content="🔎 Vaga: {cargo} | Safenet Tecnologia">
<meta name="twitter:description" content="{desc_preview}">
<style>{_CSS_FORM}</style></head><body>
<div class="shell">
  <div class="header" style="gap:14px">
    <img src="/logo.png" alt="Safenet" width="40" height="40" style="flex-shrink:0;display:block">
    <div class="header-text">
      <div class="h-brand">Safenet Tecnologia</div>
      <div class="h-sub">Gestão de Talentos</div>
    </div>
  </div>
  <div class="vaga-box">
    <div class="vaga-title">🧑‍💼 {cargo}</div>
    <div class="vaga-meta">{meta_label}</div>
    {desc_bloco}
    {tags_bloco}
    {'<div class="req-list"><b>Requisitos:</b><ul>' + req_html + '</ul></div>' if req_html else ''}
    {'<div class="req-list"><b>Diferenciais valorizados:</b><ul>' + dif_html + '</ul></div>' if dif_html else ''}
  </div>
  {erro_html}
  <form method="post" enctype="multipart/form-data" class="form-card">
    <h3>Preencha seus dados</h3>
    <div class="field"><label>Nome completo *</label>
      <input type="text" name="nome" required placeholder="Seu nome completo"></div>
    <div class="field-row">
      <div class="field"><label>E-mail *</label>
        <input type="email" name="email" required placeholder="seu@email.com"></div>
      <div class="field"><label>Telefone / WhatsApp *</label>
        <input type="tel" name="telefone" required placeholder="(21) 99999-0000"></div>
    </div>
    <div class="field-row">
      <div class="field"><label>Cidade *</label>
        <input type="text" name="cidade" required placeholder="Rio de Janeiro"></div>
      <div class="field" style="max-width:100px"><label>UF *</label>
        <input type="text" name="uf" required placeholder="RJ" maxlength="2"></div>
    </div>
    <div class="field"><label>Como soube desta vaga? *</label>
      <select name="origem" required>
        <option value="">Selecione...</option>
        <option value="LinkedIn">LinkedIn</option>
        <option value="RioVagas">RioVagas</option>
        <option value="Instagram">Instagram</option>
        <option value="Indicação">Indicação</option>
        <option value="Outro">Outro</option>
      </select></div>
    <div class="field"><label>Mensagem (opcional)</label>
      <textarea name="observacao" rows="3"
        placeholder="Conte um pouco sobre sua experiência ou motivação..."></textarea></div>
    <div class="field"><label>Currículo em PDF * (máx. 10 MB)</label>
      <div class="upload-area" onclick="document.getElementById('pdf-input').click()">
        <div class="upload-icon">📄</div>
        <div class="upload-txt">Clique para selecionar o PDF</div>
        <div class="upload-sub" id="upload-sub">PDF · máximo 10 MB</div>
        <input type="file" id="pdf-input" name="curriculo" accept=".pdf,application/pdf"
          required style="display:none" onchange="showFile(this)">
      </div></div>
    <div class="recaptcha-note">🔒 Conexão segura · Seus dados são protegidos</div>
    <button type="submit" class="btn-enviar">Enviar candidatura →</button>
  </form>
</div>
<script>
function showFile(input){{
  const f = input.files[0];
  if(!f) return;
  if(f.size > 10*1024*1024){{ alert('Arquivo muito grande. Máximo 10 MB.'); input.value=''; return; }}
  document.getElementById('upload-sub').textContent = f.name + ' (' + (f.size/1024).toFixed(0) + ' KB)';
  document.querySelector('.upload-area').style.borderColor = '#7AC143';
  document.querySelector('.upload-area').style.background = '#ebf5d6';
}}
</script>
</body></html>"""


_CSS_FORM = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--g:#7AC143;--navy:#164023;--navy2:#0d2614;--tx:#1a1a1a;--mu:#6b6b6b;
      --b1:#e3ecd9;--f0:#f4f7f1;--red:#c0392b}
body{font-family:'Segoe UI',Arial,sans-serif;background:var(--f0);color:var(--tx);min-height:100vh}
.shell{max-width:620px;margin:0 auto;padding:20px 16px 40px}
.header{background:var(--navy2);border-radius:14px;padding:18px 22px;display:flex;
        align-items:center;gap:0;margin-bottom:16px}
.h-brand{font-weight:800;font-size:16px;color:var(--g);letter-spacing:.3px}
.h-sub{font-size:11px;color:rgba(255,255,255,.45);margin-top:2px}
.vaga-box{background:#fff;border-radius:12px;border:1px solid var(--b1);padding:16px 20px;margin-bottom:14px}
.vaga-title{font-size:16px;font-weight:700;color:var(--navy);margin-bottom:4px}
.vaga-meta{font-size:12px;color:var(--mu);margin-bottom:10px}
.vaga-desc{font-size:13px;color:#333;line-height:1.6;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid var(--b1)}
.vaga-tags{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.vaga-tag{display:inline-block;font-size:12px;background:#f0f7e8;color:#2d6a0a;border:1px solid #b8dca0;border-radius:20px;padding:4px 12px;font-weight:500}
.req-list{font-size:12px;color:var(--tx);margin-top:8px}
.req-list ul{margin:4px 0 0 18px}
.erro{background:#fdecea;border:1px solid rgba(192,57,43,.3);border-radius:8px;
      padding:10px 14px;font-size:13px;color:var(--red);margin-bottom:12px}
.form-card{background:#fff;border-radius:12px;border:1px solid var(--b1);padding:20px 22px}
.form-card h3{font-size:14px;font-weight:700;color:var(--navy);margin-bottom:16px}
.field{margin-bottom:14px}
.field label{display:block;font-size:11px;font-weight:700;color:var(--mu);
             text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px}
.field input,.field select,.field textarea{width:100%;padding:10px 12px;border:1px solid var(--b1);
  border-radius:8px;font-size:13px;background:var(--f0);color:var(--tx);font-family:inherit;resize:none}
.field input:focus,.field select:focus,.field textarea:focus{outline:none;border-color:var(--g)}
.field-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.upload-area{border:2px dashed #c0cdb8;border-radius:10px;padding:22px;text-align:center;
  cursor:pointer;transition:all .14s;background:var(--f0)}
.upload-area:hover{border-color:var(--g);background:#ebf5d6}
.upload-icon{font-size:28px;margin-bottom:6px}
.upload-txt{font-size:13px;font-weight:600;color:var(--tx)}
.upload-sub{font-size:11px;color:var(--mu);margin-top:3px}
.recaptcha-note{font-size:11px;color:var(--mu);text-align:center;margin:12px 0 8px}
.btn-enviar{width:100%;padding:13px;background:var(--g);color:#fff;border:none;
  border-radius:9px;font-size:15px;font-weight:700;cursor:pointer;
  font-family:inherit;transition:background .14s}
.btn-enviar:hover{background:#5c9e2e}
.success-box{background:#fff;border-radius:14px;border:1px solid var(--b1);
  padding:40px 32px;text-align:center}
.success-ico{width:64px;height:64px;border-radius:50%;background:#ebf5d6;
  display:flex;align-items:center;justify-content:center;font-size:28px;
  color:var(--g);margin:0 auto 18px;font-weight:700}
.success-box h2{font-size:20px;color:var(--navy);margin-bottom:12px}
.success-box p{font-size:14px;color:var(--mu);line-height:1.6}
@media(max-width:500px){.field-row{grid-template-columns:1fr}}
"""


@public_router.get("/candidatura/{token}", response_class=HTMLResponse)
async def formulario_get(token: str):
    """Serve o formulário público de candidatura — sem autenticação."""
    vagas = _ler(RH_VAGAS_PATH, [])
    vaga = next((v for v in vagas if v.get("token_publico") == token), None)
    if not vaga or not vaga.get("ativa", True):
        return HTMLResponse(_html_encerrado(), status_code=410)
    if not vaga.get("token_ativo", False):
        return HTMLResponse(_html_encerrado(), status_code=410)
    return HTMLResponse(_html_formulario(vaga, token))


@public_router.post("/candidatura/{token}", response_class=HTMLResponse)
async def formulario_post(
    token: str, bt: BackgroundTasks,
    nome:      str = Form(...),
    email:     str = Form(...),
    telefone:  str = Form(...),
    cidade:    str = Form(""),
    uf:        str = Form(""),
    origem:    str = Form("Formulário público"),
    observacao:str = Form(""),
    curriculo: UploadFile = File(...),
):
    """Recebe o formulário preenchido pelo candidato."""
    # validar vaga
    vagas = _ler(RH_VAGAS_PATH, [])
    vaga = next((v for v in vagas if v.get("token_publico") == token), None)
    if not vaga or not vaga.get("ativa", True) or not vaga.get("token_ativo", False):
        return HTMLResponse(_html_encerrado(), status_code=410)

    vid = vaga["id"]
    nome  = (nome or "").strip()
    email = (email or "").strip().lower()

    # validações básicas
    if not nome or not email or "@" not in email:
        return HTMLResponse(_html_formulario(vaga, token,
            erro="Nome e e-mail válido são obrigatórios."))
    if not telefone.strip():
        return HTMLResponse(_html_formulario(vaga, token,
            erro="Telefone é obrigatório."))

    # validar PDF
    conteudo = await curriculo.read()
    if not conteudo:
        return HTMLResponse(_html_formulario(vaga, token,
            erro="Por favor, anexe seu currículo em PDF."))
    if len(conteudo) > PDF_MAX_BYTES:
        return HTMLResponse(_html_formulario(vaga, token,
            erro="Arquivo muito grande. O limite é 10 MB."))
    ct = curriculo.content_type or ""
    if "pdf" not in ct.lower() and not curriculo.filename.lower().endswith(".pdf"):
        return HTMLResponse(_html_formulario(vaga, token,
            erro="Envie apenas arquivos PDF."))

    # verificar duplicata
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    if any(c.get("email") == email and c.get("vaga_id") == vid for c in candidatos):
        return HTMLResponse(_html_formulario(vaga, token,
            erro="Este e-mail já está cadastrado para esta vaga."))

    # extrair texto do PDF
    texto_extraido = _extrair_texto_pdf(conteudo)

    with _lock:
        candidatos = _ler(RH_CANDIDATOS_PATH, [])
        novo = {
            "id":                   _proximo_id(candidatos),
            "vaga_id":              vid,
            "nome":                 nome,
            "email":                email,
            "telefone":             telefone.strip(),
            "cidade":               cidade.strip() or None,
            "uf":                   uf.strip().upper() or None,
            "origem":               origem or "Formulário público",
            "observacao":           observacao.strip() or None,
            "curriculo_texto":      texto_extraido,         # texto extraído pelo pdfplumber
            "curriculo_path":       None,                   # preenchido abaixo
            "curriculo_texto_extraido": texto_extraido,
            "pontuacao_ia":         None,
            "parecer_ia":           None,
            "criterios_ia":         [],
            "status":               "triagem",
            "motivo_reprovacao":    None,
            "data_entrada_etapa":   AGORA(),
            "sla_avisado_em":       None,
            "cadastrado_por":       "formulario_publico",
            "cadastrado_em":        AGORA(),
        }
        # salvar PDF
        try:
            path_rel = _salvar_pdf(vid, novo["id"], nome, conteudo)
            novo["curriculo_path"] = path_rel
        except Exception:
            pass  # não impede o cadastro — só perde o arquivo
        candidatos.append(novo)
        _escrever_atomico(candidatos, RH_CANDIDATOS_PATH)

    _abrir_etapa(vid, novo["id"], "triagem", "formulario_publico")
    _log_acao(vid, "formulario_publico", "candidatura_recebida",
              {"candidato_id": novo["id"], "nome": nome, "origem": origem})

    # notificar Pedro e Rafaela (regra 1)
    dest = list(set(_emails_pedro_e_rafaela()))
    bt.add_task(
        enviar_email, dest,
        f"Nova candidatura: {nome} — {vaga['cargo']}",
        f"Nova candidatura recebida pelo formulário público 🧑‍💼",
        (f"<p><b>{nome}</b> se candidatou para a vaga <b>{vaga['cargo']}</b> "
         f"pelo formulário público.</p>"
         f"<p>E-mail: {email} · Tel: {telefone} · Origem: {origem}</p>"
         f"{'<p>Currículo: texto extraído automaticamente ✓</p>' if texto_extraido else '<p>⚠ PDF escaneado — texto não extraído. Abrir o PDF manualmente.</p>'}"),
        "candidatura_publica",
        "Abrir o Kanban", RH_BASE_URL,
    )
    return HTMLResponse(_html_formulario(vaga, token, sucesso=True))


# ---------------------------------------------------------------------------
# FORMULÁRIO INTERNO DE CANDIDATURA (acesso por ID, sem token)
# Usado pelos colaboradores que clicam no bloco de vagas da intranet.
# Sem autenticação JWT — qualquer pessoa na rede interna pode acessar.
# ---------------------------------------------------------------------------

@public_router.get("/rh/vaga/{vid}", response_class=HTMLResponse)
async def formulario_interno_get(vid: int):
    """Serve o formulário de candidatura interna (por ID da vaga, sem token)."""
    vagas = _ler(RH_VAGAS_PATH, [])
    vaga = next((v for v in vagas if v["id"] == vid and v.get("ativa", True)), None)
    if not vaga:
        return HTMLResponse(_html_encerrado(), status_code=410)
    return HTMLResponse(_html_formulario_interno(vaga))


@public_router.post("/rh/vaga/{vid}", response_class=HTMLResponse)
async def formulario_interno_post(
    vid: int, bt: BackgroundTasks,
    nome:       str = Form(...),
    email:      str = Form(...),
    telefone:   str = Form(...),
    cidade:     str = Form(""),
    uf:         str = Form(""),
    observacao: str = Form(""),
    curriculo:  UploadFile = File(None),
):
    """Recebe candidatura interna de colaborador via intranet."""
    vagas = _ler(RH_VAGAS_PATH, [])
    vaga = next((v for v in vagas if v["id"] == vid and v.get("ativa", True)), None)
    if not vaga:
        return HTMLResponse(_html_encerrado(), status_code=410)

    nome  = (nome or "").strip()
    email = (email or "").strip().lower()

    if not nome or not email or "@" not in email:
        return HTMLResponse(_html_formulario_interno(vaga,
            erro="Nome e e-mail válido são obrigatórios."))
    if not telefone.strip():
        return HTMLResponse(_html_formulario_interno(vaga,
            erro="Telefone é obrigatório."))

    # PDF é opcional na candidatura interna
    conteudo = None
    texto_extraido = None
    if curriculo and curriculo.filename:
        conteudo = await curriculo.read()
        if conteudo:
            if len(conteudo) > PDF_MAX_BYTES:
                return HTMLResponse(_html_formulario_interno(vaga,
                    erro="Arquivo muito grande. O limite é 10 MB."))
            ct = curriculo.content_type or ""
            if "pdf" not in ct.lower() and not curriculo.filename.lower().endswith(".pdf"):
                return HTMLResponse(_html_formulario_interno(vaga,
                    erro="Envie apenas arquivos PDF."))
            texto_extraido = _extrair_texto_pdf(conteudo)

    # verificar duplicata
    candidatos = _ler(RH_CANDIDATOS_PATH, [])
    if any(c.get("email") == email and c.get("vaga_id") == vid for c in candidatos):
        return HTMLResponse(_html_formulario_interno(vaga,
            erro="Este e-mail já está cadastrado para esta vaga."))

    with _lock:
        candidatos = _ler(RH_CANDIDATOS_PATH, [])
        novo = {
            "id":                   _proximo_id(candidatos),
            "vaga_id":              vid,
            "nome":                 nome,
            "email":                email,
            "telefone":             telefone.strip(),
            "cidade":               cidade.strip() or None,
            "uf":                   uf.strip().upper() or None,
            "origem":               "Intranet (interno)",
            "observacao":           observacao.strip() or None,
            "curriculo_texto":      texto_extraido,
            "curriculo_path":       None,
            "curriculo_texto_extraido": texto_extraido,
            "pontuacao_ia":         None,
            "parecer_ia":           None,
            "criterios_ia":         [],
            "status":               "triagem",
            "motivo_reprovacao":    None,
            "data_entrada_etapa":   AGORA(),
            "sla_avisado_em":       None,
            "cadastrado_por":       "formulario_interno",
            "cadastrado_em":        AGORA(),
        }
        if conteudo:
            try:
                novo["curriculo_path"] = _salvar_pdf(vid, novo["id"], nome, conteudo)
            except Exception:
                pass
        candidatos.append(novo)
        _escrever_atomico(candidatos, RH_CANDIDATOS_PATH)

    _abrir_etapa(vid, novo["id"], "triagem", "formulario_interno")
    _log_acao(vid, "formulario_interno", "candidatura_interna_recebida",
              {"candidato_id": novo["id"], "nome": nome})

    dest = list(set(_emails_rh_operadores()) | set(_emails_rh_coordenadores()))
    bt.add_task(
        enviar_email, dest,
        f"Nova candidatura interna: {nome} — {vaga['cargo']}",
        "Nova candidatura recebida pela intranet 🏢",
        (f"<p><b>{nome}</b> se candidatou para a vaga <b>{vaga['cargo']}</b> "
         f"pela intranet (candidatura interna).</p>"
         f"<p>E-mail: {email} · Tel: {telefone}</p>"
         f"{'<p>Currículo: texto extraído automaticamente ✓</p>' if texto_extraido else '<p>Candidatura sem currículo anexado.</p>'}"),
        "candidatura_interna",
        "Abrir o Kanban", RH_BASE_URL,
    )
    return HTMLResponse(_html_formulario_interno(vaga, sucesso=True))


def _html_formulario_interno(vaga: dict, erro: str = None, sucesso: bool = False) -> str:
    """HTML do formulário de candidatura interna — mesmo visual do formulário público
    mas com PDF opcional e origem marcada como 'Intranet (interno)'."""
    cargo   = vaga.get("cargo", "—")
    req_html = "".join(f"<li>{r}</li>" for r in vaga.get("requisitos", []))
    dif_html = "".join(f"<li>{d}</li>" for d in vaga.get("diferenciais", []))
    erro_html = f'<div class="erro">{erro}</div>' if erro else ""

    if sucesso:
        return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Candidatura enviada — Safenet</title>
<style>{_CSS_FORM}</style></head><body>
<div class="shell">
  <div class="header" style="gap:14px">
  <img src="/logo.png" alt="Safenet" width="40" height="40" style="flex-shrink:0;display:block">
  <div class="header-text"><div class="h-brand">Safenet Tecnologia</div>
  <div class="h-sub">Candidatura Interna</div></div></div>
  <div class="success-box">
    <div class="success-ico">✓</div>
    <h2>Candidatura enviada!</h2>
    <p>Sua candidatura foi registrada com sucesso.<br>
    O RH entrará em contato pelo e-mail informado.<br><br>
    <a href="/rh/" style="font-size:13px;color:#7AC143;text-decoration:none">← Voltar para a intranet</a></p>
  </div>
</div></body></html>"""

    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Candidatura — {cargo} · Safenet</title>
<style>{_CSS_FORM}
.badge-interno{{display:inline-block;background:#edf7e8;color:#4d8d27;border:1px solid #cde8c4;
  font-size:11px;font-weight:700;padding:3px 10px;border-radius:999px;margin-bottom:10px}}
</style></head><body>
<div class="shell">
  <div class="header" style="gap:14px">
    <img src="/logo.png" alt="Safenet" width="40" height="40" style="flex-shrink:0;display:block">
    <div class="header-text">
      <div class="h-brand">Safenet Tecnologia</div>
      <div class="h-sub">Candidatura Interna</div>
    </div>
  </div>
  <div class="vaga-box">
    <span class="badge-interno">🏢 Vaga interna</span>
    <div class="vaga-title">🧑‍💼 {cargo}</div>
    <div class="vaga-meta">Rio de Janeiro · CLT · Tempo integral</div>
    {'<div class="req-list"><b>Requisitos:</b><ul>' + req_html + '</ul></div>' if req_html else ''}
    {'<div class="req-list"><b>Diferenciais valorizados:</b><ul>' + dif_html + '</ul></div>' if dif_html else ''}
  </div>
  {erro_html}
  <form method="post" enctype="multipart/form-data" class="form-card">
    <h3>Preencha seus dados para se candidatar</h3>
    <div class="field"><label>Nome completo *</label>
      <input type="text" name="nome" required placeholder="Seu nome completo"></div>
    <div class="field-row">
      <div class="field"><label>E-mail *</label>
        <input type="email" name="email" required placeholder="seu@safenettecnologia.com.br"></div>
      <div class="field"><label>Telefone / WhatsApp *</label>
        <input type="tel" name="telefone" required placeholder="(21) 99999-0000"></div>
    </div>
    <div class="field-row">
      <div class="field"><label>Cidade</label>
        <input type="text" name="cidade" placeholder="Rio de Janeiro"></div>
      <div class="field" style="max-width:100px"><label>UF</label>
        <input type="text" name="uf" placeholder="RJ" maxlength="2"></div>
    </div>
    <div class="field"><label>Mensagem / motivação (opcional)</label>
      <textarea name="observacao" rows="3"
        placeholder="Conte por que tem interesse nesta vaga..."></textarea></div>
    <div class="field"><label>Currículo em PDF (opcional · máx. 10 MB)</label>
      <div class="upload-area" onclick="document.getElementById('pdf-input').click()">
        <div class="upload-icon">📄</div>
        <div class="upload-txt">Clique para selecionar o PDF</div>
        <div class="upload-sub" id="upload-sub">PDF · máximo 10 MB · opcional</div>
        <input type="file" id="pdf-input" name="curriculo" accept=".pdf,application/pdf"
          style="display:none" onchange="showFile(this)">
      </div></div>
    <button type="submit" class="btn-enviar">Enviar candidatura →</button>
    <div style="text-align:center;margin-top:10px">
      <a href="/" style="font-size:12px;color:#6b6b68;text-decoration:none">← Voltar para a intranet</a>
    </div>
  </form>
</div>
<script>
function showFile(input){{
  const f = input.files[0];
  if(!f) return;
  if(f.size > 10*1024*1024){{ alert('Arquivo muito grande. Máximo 10 MB.'); input.value=''; return; }}
  document.getElementById('upload-sub').textContent = f.name + ' (' + (f.size/1024).toFixed(0) + ' KB)';
  document.querySelector('.upload-area').style.borderColor = '#7AC143';
  document.querySelector('.upload-area').style.background = '#ebf5d6';
}}
</script>
</body></html>"""


def _html_encerrado() -> str:
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Candidaturas encerradas — Safenet</title>
<style>{_CSS_FORM}</style></head><body>
<div class="shell">
  <div class="header" style="gap:14px">
  <img src="/logo.png" alt="Safenet" width="40" height="40" style="flex-shrink:0;display:block">
  <div class="header-text"><div class="h-brand">Safenet Tecnologia</div>
  <div class="h-sub">Gestão de Talentos</div></div></div>
  <div class="success-box">
    <div class="success-ico" style="background:#fdecea;color:#c0392b">✕</div>
    <h2 style="color:#c0392b">Candidaturas encerradas</h2>
    <p>As candidaturas para esta vaga estão encerradas no momento.<br>
    Fique de olho nas nossas redes sociais para novas oportunidades.</p>
  </div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# GERADOR DE ANÚNCIO — Fatia 6
# Usa a mesma API Anthropic da triagem IA.
# Gera texto nos 3 formatos com o link de candidatura já embutido.
# A Rafaela revisa, copia e publica manualmente nos canais.
# ---------------------------------------------------------------------------

def _montar_prompt_anuncio(vaga: dict, link_publico: str) -> tuple[str, str]:
    """Monta os prompts para geração dos 3 formatos de anúncio.
    Fatia 10: usa dados do catálogo (cargo_dados) quando disponível.
    """
    cargo  = vaga.get("cargo", "—")
    req    = "\n".join(f"- {r}" for r in vaga.get("requisitos", []))   or "A definir."
    dif    = "\n".join(f"- {d}" for d in vaga.get("diferenciais", [])) or "A definir."
    obs    = vaga.get("observacoes") or ""

    # Bloco extra com dados do catálogo
    extra = ""
    cd = vaga.get("cargo_dados") or {}
    if not cd:
        cd = _buscar_cargo_no_catalogo(vaga)
    if cd:
        tipo = cd.get("tipo", "clt")
        # Remuneração
        if tipo == "estagio" and cd.get("bolsa_fixa"):
            extra += f"\nBOLSA AUXÍLIO: R$ {cd['bolsa_fixa']:,.2f}".replace(",","X").replace(".",",").replace("X",".")
        elif cd.get("salario_min") and cd.get("salario_max"):
            mn = f"R$ {cd['salario_min']:,.2f}".replace(",","X").replace(".",",").replace("X",".")
            mx = f"R$ {cd['salario_max']:,.2f}".replace(",","X").replace(".",",").replace("X",".")
            if cd.get("multi_nivel"):
                extra += f"\nSALÁRIO: {mn} a {mx} — definido conforme nível após avaliação técnica"
            else:
                extra += f"\nSALÁRIO: {mn}"
        # Benefícios
        ben = cd.get("beneficios", {})
        ben_lista = []
        if ben.get("vale_transporte"): ben_lista.append("Vale Transporte")
        if ben.get("vale_alimentacao"): ben_lista.append(f"Vale Alimentação ({ben['vale_alimentacao']})")
        if ben.get("seguro_vida"): ben_lista.append("Seguro de Vida")
        if ben.get("sesc"): ben_lista.append("Convênio SESC")
        if ben_lista:
            extra += f"\nBENEFÍCIOS: {' / '.join(ben_lista)}"
        # Horário
        if cd.get("horario"):
            extra += f"\nHORÁRIO: {cd['horario']}"
        if cd.get("observacao"):
            extra += f"\nOBS: {cd['observacao']}"
        # Responsabilidades do catálogo
        resps = cd.get("responsabilidades", [])
        if resps:
            extra += "\n\nPRINCIPAIS RESPONSABILIDADES:\n" + "\n".join(f"- {r}" for r in resps)
        # Descrição do cargo
        desc = cd.get("descricao_vaga", "")
        if desc:
            extra += f"\n\nDESCRIÇÃO DO CARGO:\n{desc}"
        # Requisitos e diferenciais do catálogo (complementam os da vaga)
        req_cat = cd.get("requisitos_catalogo", [])
        if req_cat and req == "A definir.":
            req = "\n".join(f"- {r}" for r in req_cat)
        dif_cat = cd.get("diferenciais_catalogo", [])
        if dif_cat and dif == "A definir.":
            dif = "\n".join(f"- {d}" for d in dif_cat)

    sistema = (
        "Você é especialista em recrutamento e comunicação corporativa. "
        "Cria textos de anúncio de vaga profissionais, atrativos e objetivos. "
        "Retorna APENAS um JSON válido, sem markdown, sem texto extra."
    )

    usuario = f"""Crie os textos de anúncio para a seguinte vaga da Safenet Tecnologia:

CARGO: {cargo}
REQUISITOS ELIMINATÓRIOS:
{req}

DIFERENCIAIS VALORIZADOS:
{dif}
{extra}
{f"OBSERVAÇÕES ADICIONAIS: {obs}" if obs else ""}

LINK DE CANDIDATURA: {link_publico}

Retorne SOMENTE o seguinte JSON (sem markdown):
{{
  "linkedin": "<texto completo para LinkedIn — profissional, com emojis moderados, bullets de requisitos, salário/benefícios quando disponíveis, link no final. Máx 1500 caracteres>",
  "riov": "<texto para RioVagas/portais — formato padrão com EMPRESA, CARGO, LOCAL, REGIME, SALÁRIO, BENEFÍCIOS, REQUISITOS, DIFERENCIAIS, CANDIDATURA. Máx 800 caracteres>",
  "instagram": "<texto curto para Instagram — direto, com emojis, checkmarks, hashtags relevantes, instrução de link na bio. Máx 400 caracteres>"
}}

Regras:
- Empresa: Safenet Tecnologia (segurança eletrônica, Rio de Janeiro)
- Local: Rio de Janeiro – RJ
- Inclua o link de candidatura em todos os formatos
- No Instagram, instrua a colocar o link na bio: "{link_publico}"
- Inclua salário e benefícios nos formatos LinkedIn e RioVagas quando disponíveis
- Seja atrativo mas objetivo — textos que geram candidaturas de qualidade"""

    return sistema, usuario


def _executar_geracao_anuncio(vaga_id: int, link_publico: str):
    """Gera os anúncios em background e salva na vaga."""
    vagas = _ler(RH_VAGAS_PATH, [])
    vaga = next((v for v in vagas if v["id"] == vaga_id), None)
    if not vaga:
        return

    sistema, usuario = _montar_prompt_anuncio(vaga, link_publico)
    resposta = _chamar_anthropic(sistema, usuario, max_tokens=2000)

    if not resposta:
        with _lock:
            vagas2 = _ler(RH_VAGAS_PATH, [])
            for v in vagas2:
                if v["id"] == vaga_id:
                    v["anuncio_status"] = "erro_api"
                    break
            _escrever_atomico(vagas2, RH_VAGAS_PATH)
        return

    try:
        texto = resposta.strip()
        if texto.startswith("```"):
            texto = texto.split("```")[1]
            if texto.startswith("json"):
                texto = texto[4:]
        dados = json.loads(texto.strip())
    except (json.JSONDecodeError, IndexError):
        dados = {"linkedin": None, "riov": None, "instagram": None}

    with _lock:
        vagas2 = _ler(RH_VAGAS_PATH, [])
        for v in vagas2:
            if v["id"] == vaga_id:
                v["anuncio_linkedin"]  = dados.get("linkedin")
                v["anuncio_riov"]      = dados.get("riov")
                v["anuncio_instagram"] = dados.get("instagram")
                v["anuncio_gerado_em"] = AGORA()
                v["anuncio_link"]      = link_publico
                v["anuncio_status"]    = "ok"
                break
        _escrever_atomico(vagas2, RH_VAGAS_PATH)

    _log_acao(vaga_id, "ia", "anuncio_gerado", {"link": link_publico})


@router.post("/vagas/{vid}/anuncio")
def gerar_anuncio(vid: int, bt: BackgroundTasks,
                  user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))):
    """Dispara a geração dos textos de anúncio via IA em background.
    Usa o link público da vaga embutido em cada formato."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503,
            detail="ANTHROPIC_API_KEY não configurada.")

    vagas = _ler(RH_VAGAS_PATH, [])
    v = next((v for v in vagas if v["id"] == vid), None)
    if not v:
        raise HTTPException(status_code=404, detail="Vaga não encontrada.")

    token = v.get("token_publico")
    if not token:
        raise HTTPException(status_code=400,
            detail="Vaga sem link público. Ative o formulário de candidatura primeiro.")

    base = os.environ.get("RH_PUBLIC_BASE_URL",
                          "https://vagas.safenettecnologia.com.br")
    link_publico = f"{base}/candidatura/{token}"

    # Marcar como processando
    with _lock:
        vagas2 = _ler(RH_VAGAS_PATH, [])
        for vv in vagas2:
            if vv["id"] == vid:
                vv["anuncio_status"] = "processando"
                break
        _escrever_atomico(vagas2, RH_VAGAS_PATH)

    _log_acao(vid, user["nome"], "disparou_geracao_anuncio")
    bt.add_task(_executar_geracao_anuncio, vid, link_publico)
    return {"ok": True,
            "mensagem": "Geração de anúncio iniciada. Recarregue a vaga em alguns segundos."}


@router.get("/vagas/{vid}/anuncio")
def obter_anuncio(vid: int,
                  user=Depends(exige_papel_rh(
                      ["rh_operador","rh_coordenador","rh_solicitante"]))):
    """Retorna os textos de anúncio gerados pela IA."""
    vagas = _ler(RH_VAGAS_PATH, [])
    v = next((v for v in vagas if v["id"] == vid), None)
    if not v:
        raise HTTPException(status_code=404, detail="Vaga não encontrada.")

    base = os.environ.get("RH_PUBLIC_BASE_URL",
                          "https://vagas.safenettecnologia.com.br")
    token = v.get("token_publico")
    return {
        "vaga_id":          vid,
        "cargo":            v.get("cargo"),
        "anuncio_linkedin": v.get("anuncio_linkedin"),
        "anuncio_riov":     v.get("anuncio_riov"),
        "anuncio_instagram":v.get("anuncio_instagram"),
        "anuncio_gerado_em":v.get("anuncio_gerado_em"),
        "anuncio_status":   v.get("anuncio_status"),
        "link_publico":     f"{base}/candidatura/{token}" if token else None,
        "processando":      v.get("anuncio_status") == "processando",
        "anthropic_configurado": bool(ANTHROPIC_API_KEY),
    }


@router.patch("/vagas/{vid}/anuncio")
def editar_anuncio(vid: int, dados: dict, bt: BackgroundTasks,
                   user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))):
    """Permite editar manualmente qualquer um dos 3 formatos de anúncio."""
    with _lock:
        vagas = _ler(RH_VAGAS_PATH, [])
        v = next((v for v in vagas if v["id"] == vid), None)
        if not v:
            raise HTTPException(status_code=404, detail="Vaga não encontrada.")
        for campo in ("anuncio_linkedin", "anuncio_riov", "anuncio_instagram"):
            if campo in dados:
                v[campo] = dados[campo]
        v["anuncio_editado_por"] = user["nome"]
        v["anuncio_editado_em"]  = AGORA()
        _escrever_atomico(vagas, RH_VAGAS_PATH)
    _log_acao(vid, user["nome"], "editou_anuncio")
    return {"ok": True}


# ---------------------------------------------------------------------------
# TOKEN PÚBLICO — ativar/desativar e gerar link
# ---------------------------------------------------------------------------
@router.patch("/vagas/{vid}/token")
def toggle_token(vid: int,
                 user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))):
    """Ativa ou desativa o formulário público da vaga (alterna o estado atual)."""
    with _lock:
        vagas = _ler(RH_VAGAS_PATH, [])
        v = next((v for v in vagas if v["id"] == vid), None)
        if not v:
            raise HTTPException(status_code=404, detail="Vaga não encontrada.")
        # se ainda não tem token, gera um
        if not v.get("token_publico"):
            v["token_publico"] = secrets.token_urlsafe(32)
        v["token_ativo"] = not v.get("token_ativo", False)
        _escrever_atomico(vagas, RH_VAGAS_PATH)
    _log_acao(vid, user["nome"], "toggle_token",
              {"token_ativo": v["token_ativo"]})
    base = os.environ.get("RH_PUBLIC_BASE_URL", "https://vagas.safenettecnologia.com.br")
    return {
        "ok": True,
        "token_ativo": v["token_ativo"],
        "link_publico": f"{base}/candidatura/{v['token_publico']}" if v["token_ativo"] else None,
    }


@router.get("/vagas/{vid}/link")
def obter_link_publico(vid: int,
                       user=Depends(exige_papel_rh(["rh_operador","rh_coordenador","rh_solicitante"]))):
    """Retorna o link público de candidatura da vaga."""
    vagas = _ler(RH_VAGAS_PATH, [])
    v = next((v for v in vagas if v["id"] == vid), None)
    if not v:
        raise HTTPException(status_code=404, detail="Vaga não encontrada.")
    base = os.environ.get("RH_PUBLIC_BASE_URL", "https://vagas.safenettecnologia.com.br")
    token = v.get("token_publico")
    return {
        "token_publico": token,
        "token_ativo":   v.get("token_ativo", False),
        "link_publico":  f"{base}/candidatura/{token}" if token else None,
    }


# ---------------------------------------------------------------------------
# Thread SLA — ÚLTIMA linha do módulo (mesma convenção do licitacoes_routes.py)
# ---------------------------------------------------------------------------
threading.Thread(target=_loop_sla_rh, daemon=True, name="sla-rh").start()
