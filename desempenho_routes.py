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
import uuid
import smtplib
import ssl
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from auth_routes import usuario_logado, exige_papel_rh
import auth_store

router        = APIRouter(prefix="/api/rh/desempenho",        tags=["desempenho"])
public_router = APIRouter(prefix="/api/rh/desempenho/public", tags=["desempenho-public"])

# ---------------------------------------------------------------------------
# PATHS — mesmo padrão BASE = Path(__file__).parent do rh_routes.py
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent
DESEMPENHO_COLABORADORES_PATH = BASE / "data" / "desempenho_colaboradores.json"
DESEMPENHO_CRITERIOS_PATH     = BASE / "data" / "desempenho_criterios.json"
DESEMPENHO_CICLOS_PATH        = BASE / "data" / "desempenho_ciclos.json"
DESEMPENHO_EMAIL_LOG_PATH     = BASE / "data" / "desempenho_email_log.json"
DESEMPENHO_GATILHOS_LOG_PATH  = BASE / "data" / "desempenho_gatilhos_log.json"

_lock       = threading.Lock()   # lock geral (ciclos, critérios, e-mail, gatilhos)
_lock_colab = threading.Lock()   # lock dedicado para colaboradores (evita bloqueio pelo thread de gatilhos)

AGORA = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------------------------------------------------------
# CONSTANTES DE DOMÍNIO — alinhadas com MAP-RH-002
# ---------------------------------------------------------------------------
SETORES_VALIDOS = [
    "Operacional", "Monitoramento", "Projetos", "TI",
    "Administrativo", "Licitação",
]

VINCULOS_VALIDOS = ["CLT", "PJ", "Estagiário"]

TIPOS_CICLO_VALIDOS = ["experiencia_1", "experiencia_2", "semestral"]

TIPOS_CICLO_LABELS = {
    "experiencia_1": "Avaliação de Experiência — 35º dia",
    "experiencia_2": "Avaliação de Experiência — 80º dia",
    "semestral":     "Avaliação Semestral",
}

# Bloco 1: critérios comportamentais (fixos — iguais para todos os colaboradores)
CRITERIOS_COMPORTAMENTAIS = [
    {"id": "comprometimento", "descricao": "Comprometimento e assiduidade"},
    {"id": "trabalho_equipe", "descricao": "Trabalho em equipe e colaboração"},
    {"id": "comunicacao",     "descricao": "Comunicação e relacionamento interpessoal"},
    {"id": "proatividade",    "descricao": "Proatividade e iniciativa"},
    {"id": "normas",          "descricao": "Cumprimento de normas e procedimentos"},
]

NOTAS_LABELS = {
    1: "Muito abaixo do esperado",
    2: "Abaixo do esperado",
    3: "Dentro do esperado",
    4: "Acima do esperado",
    5: "Muito acima do esperado",
}

# Gatilhos automáticos de ciclo (Fatia F)
# (tipo, dia_alvo_desde_admissao, janela_dias)
GATILHOS_EXPERIENCIA = [
    ("experiencia_1", 35, 4),
    ("experiencia_2", 80, 4),
]
GATILHO_SEMESTRAL_INICIO_DIA = 90   # efetivação após período de experiência
GATILHO_SEMESTRAL_INTERVALO  = 180  # a cada 6 meses
GATILHO_SEMESTRAL_JANELA     = 4    # margem de ±4 dias para a checagem diária
GATILHO_PRAZO_DIAS           = 14   # prazo padrão para preenchimento do formulário


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


class NovoCriterioIn(BaseModel):
    descricao: str
    setor: str                          # vide SETORES_VALIDOS
    aplicavel_a: list = []             # ex.: ["Técnico", "Instalador"] — vazio = todos do setor


class EditarCriterioIn(BaseModel):
    descricao: Optional[str] = None
    setor: Optional[str] = None
    aplicavel_a: Optional[list] = None
    ativo: Optional[bool] = None


# ---------------------------------------------------------------------------
# MODELOS — CICLOS (Fatia C)
# ---------------------------------------------------------------------------
class NovoCicloIn(BaseModel):
    colaborador_id: int
    tipo: str                   # experiencia_1 | experiencia_2 | semestral
    prazo: str                  # AAAA-MM-DD
    enviar_email: bool = True


class AutoavaliacaoIn(BaseModel):
    criterios_comportamentais: list
    criterios_tecnicos: list
    observacao_geral: str = ""


class AvaliacaoAvaliadorIn(BaseModel):
    criterios_comportamentais: list   # [{criterio_id, nota, observacao}]
    criterios_tecnicos: list
    pontos_fortes: str = ""
    pontos_melhoria: str = ""
    plano_acao: str = ""


class AvaliacaoSupervisorIn(BaseModel):
    criterios_comportamentais: list   # co-avaliação dos critérios comportamentais
    criterios_tecnicos: list
    observacao_geral: str = ""


# ---------------------------------------------------------------------------
# E-MAIL — mesmo padrão de licitacoes_routes.py
# Nunca levanta exceção pro chamador; toda tentativa vai pro log.
# ---------------------------------------------------------------------------
def _base_url() -> str:
    return os.environ.get("RH_PUBLIC_BASE_URL",
           os.environ.get("INTRANET_BASE_URL", "http://192.168.0.171:8081"))


def _log_email(evento: str, destinatario: str, sucesso: bool, detalhe: str = ""):
    with _lock:
        log = _ler(DESEMPENHO_EMAIL_LOG_PATH, [])
        log.append({
            "id":           _proximo_id(log),
            "evento":       evento,
            "destinatario": destinatario,
            "sucesso":      sucesso,
            "detalhe":      detalhe,
            "enviado_em":   AGORA(),
        })
        if len(log) > 500:
            log = log[-500:]
        _escrever_atomico(log, DESEMPENHO_EMAIL_LOG_PATH)


def _enviar_email_autoavaliacao(colaborador: dict, ciclo: dict) -> bool:
    host     = os.environ.get("INTRANET_SMTP_HOST", "")
    port     = int(os.environ.get("INTRANET_SMTP_PORT", "465"))
    user     = os.environ.get("INTRANET_SMTP_USER", "")
    pw       = os.environ.get("INTRANET_SMTP_PASS", "")
    starttls = os.environ.get("INTRANET_SMTP_STARTTLS", "false").lower() == "true"
    from_nome = os.environ.get("INTRANET_SMTP_FROM_NOME", "Gestão RH — Safenet")

    dest = colaborador.get("email", "")
    if not host or not dest:
        _log_email("autoavaliacao_link", dest, False,
                   "SMTP não configurado ou colaborador sem e-mail.")
        return False

    link = f"{_base_url()}/rh/autoavaliacao.html?token={ciclo['token_auto']}"
    tipo_label = TIPOS_CICLO_LABELS.get(ciclo["tipo"], ciclo["tipo"])
    nome  = colaborador.get("nome", "Colaborador")
    prazo = ciclo.get("prazo", "")

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:580px;margin:0 auto">
      <div style="background:#0D2614;padding:20px 28px;border-radius:10px 10px 0 0">
        <span style="color:#7AC143;font-size:20px;font-weight:700">SAFENET TECNOLOGIA</span>
        <span style="color:#fff;font-size:13px;margin-left:12px">· Gestão de Pessoas</span>
      </div>
      <div style="background:#f8faf7;padding:28px;border:1px solid #dde8d5;border-top:none;border-radius:0 0 10px 10px">
        <p style="font-size:15px;margin:0 0 12px">Olá, <strong>{nome}</strong>!</p>
        <p style="font-size:13px;color:#444;margin:0 0 20px">
          Chegou o momento da sua <strong>{tipo_label}</strong>.
          Reserve alguns minutos para preencher sua autoavaliação com sinceridade —
          ela é a base do seu desenvolvimento na Safenet.
        </p>
        <div style="text-align:center;margin:24px 0">
          <a href="{link}"
             style="background:#7AC143;color:#fff;padding:14px 32px;border-radius:8px;
                    font-weight:700;font-size:14px;text-decoration:none;display:inline-block">
            ✏ Preencher minha autoavaliação
          </a>
        </div>
        <p style="font-size:11px;color:#888;margin:16px 0 0">
          Prazo: <strong>{prazo}</strong> &nbsp;|&nbsp;
          Este link é pessoal e intransferível. Dúvidas? Fale com o RH.
        </p>
        <hr style="border:none;border-top:1px solid #dde8d5;margin:20px 0">
        <p style="font-size:11px;color:#aaa;margin:0">
          Safenet Tecnologia · Sistema de Avaliação de Desempenho
        </p>
      </div>
    </div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Autoavaliação — {tipo_label} | Safenet"
    msg["From"]    = f"{from_nome} <{user}>"
    msg["To"]      = dest
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if starttls:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, pw)
                s.sendmail(user, [dest], msg.as_string())
        else:
            with smtplib.SMTP_SSL(host, port,
                                   context=ssl.create_default_context(),
                                   timeout=15) as s:
                s.login(user, pw)
                s.sendmail(user, [dest], msg.as_string())
        _log_email("autoavaliacao_link", dest, True, link)
        return True
    except Exception as e:
        _log_email("autoavaliacao_link", dest, False, str(e))
        return False


def _enviar_email_avaliador(avaliador_email: str, avaliador_nome: str,
                             colaborador: dict, ciclo: dict) -> bool:
    """E-mail ao avaliador com link para preencher a avaliação."""
    host      = os.environ.get("INTRANET_SMTP_HOST", "")
    port      = int(os.environ.get("INTRANET_SMTP_PORT", "465"))
    user      = os.environ.get("INTRANET_SMTP_USER", "")
    pw        = os.environ.get("INTRANET_SMTP_PASS", "")
    starttls  = os.environ.get("INTRANET_SMTP_STARTTLS", "false").lower() == "true"
    from_nome = os.environ.get("INTRANET_SMTP_FROM_NOME", "Gestão RH — Safenet")

    if not host or not avaliador_email:
        _log_email("avaliador_link", avaliador_email, False, "SMTP não configurado ou avaliador sem e-mail.")
        return False

    link       = f"{_base_url()}/rh/avaliacao_avaliador.html?token={ciclo['token_aval']}"
    tipo_label = TIPOS_CICLO_LABELS.get(ciclo["tipo"], ciclo["tipo"])
    colab_nome = colaborador.get("nome", "colaborador")
    prazo      = ciclo.get("prazo", "")

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:580px;margin:0 auto">
      <div style="background:#0D2614;padding:20px 28px;border-radius:10px 10px 0 0">
        <span style="color:#7AC143;font-size:20px;font-weight:700">SAFENET TECNOLOGIA</span>
        <span style="color:#fff;font-size:13px;margin-left:12px">· Gestão de Pessoas</span>
      </div>
      <div style="background:#f8faf7;padding:28px;border:1px solid #dde8d5;border-top:none;border-radius:0 0 10px 10px">
        <p style="font-size:15px;margin:0 0 12px">Olá, <strong>{avaliador_nome}</strong>!</p>
        <p style="font-size:13px;color:#444;margin:0 0 20px">
          A autoavaliação de <strong>{colab_nome}</strong> ({tipo_label}) foi concluída.
          Acesse o link abaixo para preencher a sua avaliação — você poderá ver as respostas
          do colaborador como referência.
        </p>
        <div style="text-align:center;margin:24px 0">
          <a href="{link}"
             style="background:#7AC143;color:#fff;padding:14px 32px;border-radius:8px;
                    font-weight:700;font-size:14px;text-decoration:none;display:inline-block">
            📋 Preencher minha avaliação
          </a>
        </div>
        <p style="font-size:11px;color:#888;margin:16px 0 0">
          Prazo: <strong>{prazo}</strong> &nbsp;|&nbsp; Link pessoal e intransferível.
        </p>
        <hr style="border:none;border-top:1px solid #dde8d5;margin:20px 0">
        <p style="font-size:11px;color:#aaa;margin:0">Safenet Tecnologia · Sistema de Avaliação de Desempenho</p>
      </div>
    </div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Avaliação de {colab_nome} — {tipo_label} | Safenet"
    msg["From"]    = f"{from_nome} <{user}>"
    msg["To"]      = avaliador_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if starttls:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, pw); s.sendmail(user, [avaliador_email], msg.as_string())
        else:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as s:
                s.login(user, pw); s.sendmail(user, [avaliador_email], msg.as_string())
        _log_email("avaliador_link", avaliador_email, True, link)
        return True
    except Exception as e:
        _log_email("avaliador_link", avaliador_email, False, str(e))
        return False


def _enviar_email_supervisor(sup_email: str, sup_nome: str,
                              colaborador: dict, ciclo: dict) -> bool:
    """E-mail ao supervisor de campo com link para co-avaliação."""
    host      = os.environ.get("INTRANET_SMTP_HOST", "")
    port      = int(os.environ.get("INTRANET_SMTP_PORT", "465"))
    user      = os.environ.get("INTRANET_SMTP_USER", "")
    pw        = os.environ.get("INTRANET_SMTP_PASS", "")
    starttls  = os.environ.get("INTRANET_SMTP_STARTTLS", "false").lower() == "true"
    from_nome = os.environ.get("INTRANET_SMTP_FROM_NOME", "Gestão RH — Safenet")

    if not host or not sup_email:
        _log_email("supervisor_link", sup_email, False, "SMTP não configurado ou supervisor sem e-mail.")
        return False

    link       = f"{_base_url()}/rh/avaliacao_supervisor.html?token={ciclo['token_sup']}"
    tipo_label = TIPOS_CICLO_LABELS.get(ciclo["tipo"], ciclo["tipo"])
    colab_nome = colaborador.get("nome", "colaborador")
    prazo      = ciclo.get("prazo", "")

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:580px;margin:0 auto">
      <div style="background:#0D2614;padding:20px 28px;border-radius:10px 10px 0 0">
        <span style="color:#7AC143;font-size:20px;font-weight:700">SAFENET TECNOLOGIA</span>
        <span style="color:#fff;font-size:13px;margin-left:12px">· Gestão de Pessoas</span>
      </div>
      <div style="background:#f8faf7;padding:28px;border:1px solid #dde8d5;border-top:none;border-radius:0 0 10px 10px">
        <p style="font-size:15px;margin:0 0 12px">Olá, <strong>{sup_nome}</strong>!</p>
        <p style="font-size:13px;color:#444;margin:0 0 20px">
          Como supervisor de campo de <strong>{colab_nome}</strong>, sua perspectiva é importante
          na {tipo_label}. Acesse o link para registrar sua co-avaliação.
        </p>
        <div style="text-align:center;margin:24px 0">
          <a href="{link}"
             style="background:#7AC143;color:#fff;padding:14px 32px;border-radius:8px;
                    font-weight:700;font-size:14px;text-decoration:none;display:inline-block">
            🏆 Preencher co-avaliação
          </a>
        </div>
        <p style="font-size:11px;color:#888;margin:16px 0 0">
          Prazo: <strong>{prazo}</strong> &nbsp;|&nbsp; Link pessoal e intransferível.
        </p>
        <hr style="border:none;border-top:1px solid #dde8d5;margin:20px 0">
        <p style="font-size:11px;color:#aaa;margin:0">Safenet Tecnologia · Sistema de Avaliação de Desempenho</p>
      </div>
    </div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Co-avaliação de {colab_nome} — {tipo_label} | Safenet"
    msg["From"]    = f"{from_nome} <{user}>"
    msg["To"]      = sup_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if starttls:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, pw); s.sendmail(user, [sup_email], msg.as_string())
        else:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as s:
                s.login(user, pw); s.sendmail(user, [sup_email], msg.as_string())
        _log_email("supervisor_link", sup_email, True, link)
        return True
    except Exception as e:
        _log_email("supervisor_link", sup_email, False, str(e))
        return False


# ---------------------------------------------------------------------------
# CÁLCULO DO RESULTADO CONSOLIDADO (Fatia E)
# ---------------------------------------------------------------------------
def _classificar(nota: float) -> str:
    if nota is None: return "–"
    if nota >= 4.5: return "Muito acima do esperado"
    if nota >= 3.5: return "Acima do esperado"
    if nota >= 3.0: return "Dentro do esperado"
    if nota >= 2.0: return "Abaixo do esperado"
    return "Muito abaixo do esperado"


def _calcular_resultado(ciclo: dict, crit_tecnicos_setor: list) -> dict:
    """Consolida notas do avaliador (+ supervisor se houver) por critério.
    Pesos: com supervisor → avaliador 60% / supervisor 40%.
    Média geral: 50% comportamental + 50% técnica.
    """
    aa  = ciclo.get("autoavaliacao", {})
    av  = ciclo.get("avaliacao_avaliador", {})
    sup = ciclo.get("avaliacao_supervisor")
    tem_sup = bool(sup)

    PESO_AV  = 0.6 if tem_sup else 1.0
    PESO_SUP = 0.4 if tem_sup else 0.0

    def idx(lst): return {str(i.get("criterio_id","")): i for i in (lst or [])}

    aa_comp  = idx(aa.get("criterios_comportamentais", []))
    aa_tec   = idx(aa.get("criterios_tecnicos", []))
    av_comp  = idx(av.get("criterios_comportamentais", []))
    av_tec   = idx(av.get("criterios_tecnicos", []))
    sup_comp = idx(sup.get("criterios_comportamentais", [])) if sup else {}
    sup_tec  = idx(sup.get("criterios_tecnicos", [])) if sup else {}

    por_criterio = []

    # Comportamentais (fixos)
    for c in CRITERIOS_COMPORTAMENTAIS:
        cid = c["id"]
        n_auto = (aa_comp.get(cid) or {}).get("nota")
        n_aval = (av_comp.get(cid) or {}).get("nota")
        n_sup  = (sup_comp.get(cid) or {}).get("nota") if tem_sup else None
        if n_aval is None:
            continue
        n_final = round(n_aval * PESO_AV + (n_sup * PESO_SUP if n_sup else 0), 2)
        por_criterio.append({"criterio_id": cid, "descricao": c["descricao"],
            "tipo": "comportamental", "nota_auto": n_auto,
            "nota_aval": n_aval, "nota_sup": n_sup, "nota_final": n_final})

    # Técnicos (do setor)
    for c in crit_tecnicos_setor:
        cid = str(c.get("id",""))
        n_auto = (aa_tec.get(cid) or {}).get("nota")
        n_aval = (av_tec.get(cid) or {}).get("nota")
        n_sup  = (sup_tec.get(cid) or {}).get("nota") if tem_sup else None
        if n_aval is None:
            continue
        n_final = round(n_aval * PESO_AV + (n_sup * PESO_SUP if n_sup else 0), 2)
        por_criterio.append({"criterio_id": cid, "descricao": c.get("descricao",""),
            "tipo": "tecnico", "nota_auto": n_auto,
            "nota_aval": n_aval, "nota_sup": n_sup, "nota_final": n_final})

    def media(lst, campo):
        vals = [c[campo] for c in lst if c.get(campo) is not None]
        return round(sum(vals)/len(vals), 2) if vals else None

    comp = [c for c in por_criterio if c["tipo"] == "comportamental"]
    tec  = [c for c in por_criterio if c["tipo"] == "tecnico"]

    m_comp = media(comp, "nota_final")
    m_tec  = media(tec,  "nota_final")
    partes = [x for x in [m_comp, m_tec] if x is not None]
    m_geral = round(sum(partes)/len(partes), 2) if partes else None

    return {
        "media_comportamental_auto":  media(comp, "nota_auto"),
        "media_comportamental_aval":  media(comp, "nota_aval"),
        "media_comportamental_sup":   media(comp, "nota_sup") if tem_sup else None,
        "media_comportamental_final": m_comp,
        "media_tecnica_auto":         media(tec, "nota_auto"),
        "media_tecnica_aval":         media(tec, "nota_aval"),
        "media_tecnica_sup":          media(tec, "nota_sup") if tem_sup else None,
        "media_tecnica_final":        m_tec,
        "media_geral_final":          m_geral,
        "classificacao":              _classificar(m_geral),
        "pesos":                      {"avaliador": PESO_AV, "supervisor": PESO_SUP},
        "com_supervisor":             tem_sup,
        "por_criterio":               por_criterio,
    }


def _enviar_email_resultado(colaborador: dict, ciclo: dict, resultado: dict) -> bool:
    """Notifica o colaborador com a classificação final."""
    host      = os.environ.get("INTRANET_SMTP_HOST", "")
    port      = int(os.environ.get("INTRANET_SMTP_PORT", "465"))
    user_smtp = os.environ.get("INTRANET_SMTP_USER", "")
    pw        = os.environ.get("INTRANET_SMTP_PASS", "")
    starttls  = os.environ.get("INTRANET_SMTP_STARTTLS", "false").lower() == "true"
    from_nome = os.environ.get("INTRANET_SMTP_FROM_NOME", "Gestão RH — Safenet")

    dest = colaborador.get("email", "")
    if not host or not dest:
        _log_email("resultado", dest, False, "SMTP não configurado ou colaborador sem e-mail.")
        return False

    tipo_label   = TIPOS_CICLO_LABELS.get(ciclo.get("tipo",""), ciclo.get("tipo",""))
    nome         = colaborador.get("nome", "Colaborador")
    classificacao = resultado.get("classificacao","–")
    m_geral      = resultado.get("media_geral_final")
    nota_str     = f"{m_geral:.2f}" if m_geral else "–"

    COR_CLASS = {
        "Muito acima do esperado": "#2d6a0a",
        "Acima do esperado":       "#4a7a2a",
        "Dentro do esperado":      "#856404",
        "Abaixo do esperado":      "#a04000",
        "Muito abaixo do esperado":"#a00000",
    }
    cor = COR_CLASS.get(classificacao, "#333")

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:580px;margin:0 auto">
      <div style="background:#0D2614;padding:20px 28px;border-radius:10px 10px 0 0">
        <span style="color:#7AC143;font-size:20px;font-weight:700">SAFENET TECNOLOGIA</span>
        <span style="color:#fff;font-size:13px;margin-left:12px">· Gestão de Pessoas</span>
      </div>
      <div style="background:#f8faf7;padding:28px;border:1px solid #dde8d5;border-top:none;border-radius:0 0 10px 10px">
        <p style="font-size:15px;margin:0 0 12px">Olá, <strong>{nome}</strong>!</p>
        <p style="font-size:13px;color:#444;margin:0 0 20px">
          Sua <strong>{tipo_label}</strong> foi concluída. Confira o resultado abaixo.
        </p>
        <div style="background:#fff;border:1px solid #dde8d5;border-radius:10px;padding:20px;text-align:center;margin:0 0 20px">
          <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Classificação final</div>
          <div style="font-size:28px;font-weight:800;color:{cor};margin-bottom:4px">{nota_str}</div>
          <div style="font-size:16px;font-weight:700;color:{cor}">{classificacao}</div>
        </div>
        <p style="font-size:12px;color:#666;margin:0">
          O RH e seu avaliador entrarão em contato para discutir os próximos passos
          e o plano de desenvolvimento.
        </p>
        <hr style="border:none;border-top:1px solid #dde8d5;margin:20px 0">
        <p style="font-size:11px;color:#aaa;margin:0">Safenet Tecnologia · Sistema de Avaliação de Desempenho</p>
      </div>
    </div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Resultado da {tipo_label} | Safenet"
    msg["From"]    = f"{from_nome} <{user_smtp}>"
    msg["To"]      = dest
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if starttls:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user_smtp, pw); s.sendmail(user_smtp, [dest], msg.as_string())
        else:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as s:
                s.login(user_smtp, pw); s.sendmail(user_smtp, [dest], msg.as_string())
        _log_email("resultado", dest, True, classificacao)
        return True
    except Exception as e:
        _log_email("resultado", dest, False, str(e))
        return False


# ---------------------------------------------------------------------------
# GATILHOS AUTOMÁTICOS (Fatia F)
# Thread daemon que roda diariamente e dispara ciclos automaticamente.
# Padrão idêntico ao sla-diario do licitacoes_routes.py.
# ---------------------------------------------------------------------------
def _log_gatilho(colab_id: int, colab_nome: str, tipo: str,
                 ciclo_id: int, motivo: str = ""):
    with _lock:
        log = _ler(DESEMPENHO_GATILHOS_LOG_PATH, [])
        log.append({
            "id":           _proximo_id(log),
            "colaborador_id":   colab_id,
            "colaborador_nome": colab_nome,
            "tipo":         tipo,
            "ciclo_id":     ciclo_id,
            "motivo":       motivo,
            "disparado_em": AGORA(),
        })
        if len(log) > 1000:
            log = log[-1000:]
        _escrever_atomico(log, DESEMPENHO_GATILHOS_LOG_PATH)


def _ja_disparado_experiencia(colab_id: int, tipo: str, ciclos: list) -> bool:
    """Para experiencia_1/2: qualquer ciclo não-cancelado já basta."""
    return any(c.get("colaborador_id") == colab_id
               and c.get("tipo") == tipo
               and c.get("status") != "cancelado"
               for c in ciclos)


def _ja_disparado_semestral(colab_id: int, semestre_key: str, ciclos: list) -> bool:
    """Para semestral: verifica pelo campo periodo_ref (ex: '2026-S3')."""
    return any(c.get("colaborador_id") == colab_id
               and c.get("tipo") == "semestral"
               and c.get("periodo_ref") == semestre_key
               and c.get("status") != "cancelado"
               for c in ciclos)


def _criar_ciclo_automatico(colaborador: dict, tipo: str,
                             ciclos: list, periodo_ref: str = "") -> dict | None:
    """Cria um ciclo diretamente (sem HTTP). Retorna o ciclo criado ou None."""
    hoje = date.today()
    prazo = (hoje + timedelta(days=GATILHO_PRAZO_DIAS)).strftime("%Y-%m-%d")

    novo = {
        "id":             _proximo_id(ciclos),
        "colaborador_id": colaborador["id"],
        "tipo":           tipo,
        "prazo":          prazo,
        "periodo_ref":    periodo_ref,
        "status":         "pendente",
        "token_auto":     str(uuid.uuid4()),
        "token_aval":     str(uuid.uuid4()),
        "token_sup":      str(uuid.uuid4()) if colaborador.get("supervisor_id") else None,
        "email_enviado":  False,
        "email_enviado_em": None,
        "autoavaliacao":        None,
        "avaliacao_avaliador":  None,
        "avaliacao_supervisor": None,
        "criado_por":     "sistema (gatilho automático)",
        "criado_em":      AGORA(),
        "encerrado_em":   None,
        "origem":         "automatico",
    }
    ciclos.append(novo)
    return novo


def _verificar_e_disparar_gatilhos() -> dict:
    """Verifica todos os colaboradores e dispara ciclos quando necessário.
    Chamada pela thread daemon e pela rota /admin/gatilhos/rodar-agora.
    Retorna sumário da execução.
    """
    hoje = date.today()
    disparados = []
    erros      = []

    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    ativos = [c for c in colaboradores if c.get("ativo", True) and c.get("data_admissao")]

    with _lock:
        ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])

        for colab in ativos:
            try:
                admissao = datetime.strptime(colab["data_admissao"], "%Y-%m-%d").date()
            except ValueError:
                continue

            dias = (hoje - admissao).days
            colab_id = colab["id"]

            # ── Avaliações de experiência ──────────────────────────────────
            for tipo, dia_alvo, janela in GATILHOS_EXPERIENCIA:
                if abs(dias - dia_alvo) <= janela:
                    if _ja_disparado_experiencia(colab_id, tipo, ciclos):
                        continue
                    novo = _criar_ciclo_automatico(colab, tipo, ciclos)
                    disparados.append({
                        "colaborador": colab["nome"], "tipo": tipo,
                        "ciclo_id": novo["id"], "dia": dias
                    })
                    _log_gatilho(colab_id, colab["nome"], tipo, novo["id"],
                                 f"dia {dias} (alvo {dia_alvo})")

            # ── Avaliação semestral ────────────────────────────────────────
            if dias >= GATILHO_SEMESTRAL_INICIO_DIA:
                dias_efetivo = dias - GATILHO_SEMESTRAL_INICIO_DIA
                for n in range(1, 30):           # até 15 anos
                    alvo = n * GATILHO_SEMESTRAL_INTERVALO
                    if abs(dias_efetivo - alvo) <= GATILHO_SEMESTRAL_JANELA:
                        semestre_key = f"{hoje.year}-S{n}"
                        if _ja_disparado_semestral(colab_id, semestre_key, ciclos):
                            break
                        novo = _criar_ciclo_automatico(
                            colab, "semestral", ciclos, periodo_ref=semestre_key)
                        disparados.append({
                            "colaborador": colab["nome"], "tipo": "semestral",
                            "periodo": semestre_key, "ciclo_id": novo["id"], "dia": dias
                        })
                        _log_gatilho(colab_id, colab["nome"], "semestral", novo["id"],
                                     f"semestre {semestre_key} (dia {dias})")
                        break

        if disparados:
            _escrever_atomico(ciclos, DESEMPENHO_CICLOS_PATH)

    # Enviar e-mails fora do lock
    for item in disparados:
        try:
            ciclo_id = item["ciclo_id"]
            ciclos_att = _ler(DESEMPENHO_CICLOS_PATH, [])
            ciclo = next((c for c in ciclos_att if c["id"] == ciclo_id), None)
            colab = next((c for c in colaboradores if c["id"] == (ciclo or {}).get("colaborador_id")), None)
            if ciclo and colab:
                ok = _enviar_email_autoavaliacao(colab, ciclo)
                if ok:
                    with _lock:
                        ciclos2 = _ler(DESEMPENHO_CICLOS_PATH, [])
                        c = next((c for c in ciclos2 if c["id"] == ciclo_id), None)
                        if c:
                            c["email_enviado"]    = True
                            c["email_enviado_em"] = AGORA()
                            _escrever_atomico(ciclos2, DESEMPENHO_CICLOS_PATH)
        except Exception as e:
            erros.append(str(e))

    return {
        "data_checagem": str(hoje),
        "colaboradores_verificados": len(ativos),
        "ciclos_disparados": len(disparados),
        "detalhes": disparados,
        "erros": erros,
    }


def _rotina_gatilhos_diaria():
    """Thread daemon — roda verificação uma vez por dia."""
    # Aguardar 30 s após o startup para o app estar completamente carregado
    time.sleep(30)
    while True:
        try:
            resultado = _verificar_e_disparar_gatilhos()
            if resultado["ciclos_disparados"]:
                print(f"[desempenho] Gatilhos: {resultado['ciclos_disparados']} ciclo(s) disparado(s) — {resultado['data_checagem']}")
        except Exception as e:
            print(f"[desempenho] Erro na rotina de gatilhos: {e}")
        time.sleep(86400)  # 24 horas


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

    with _lock_colab:
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
# CICLOS DE AVALIAÇÃO (Fatia C)
# ---------------------------------------------------------------------------
@router.get("/ciclos")
def listar_ciclos(
    colaborador_id: Optional[int] = None,
    status: Optional[str] = None,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Lista ciclos com filtros opcionais."""
    ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
    if colaborador_id is not None:
        ciclos = [c for c in ciclos if c.get("colaborador_id") == colaborador_id]
    if status:
        ciclos = [c for c in ciclos if c.get("status") == status]

    # enriquecer com nome do colaborador
    colab_map = {c["id"]: c for c in _ler(DESEMPENHO_COLABORADORES_PATH, [])}
    for c in ciclos:
        col = colab_map.get(c.get("colaborador_id"), {})
        c["_colaborador_nome"] = col.get("nome", "–")
        c["_colaborador_setor"] = col.get("setor", "–")
        c["_tipo_label"] = TIPOS_CICLO_LABELS.get(c.get("tipo", ""), c.get("tipo", ""))

    ciclos = sorted(ciclos, key=lambda c: c.get("criado_em", ""), reverse=True)
    return {"ciclos": ciclos, "total": len(ciclos)}


@router.get("/ciclos/{cid}")
def obter_ciclo(
    cid: int,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
    c = next((c for c in ciclos if c["id"] == cid), None)
    if not c:
        raise HTTPException(status_code=404, detail="Ciclo não encontrado.")
    return {"ciclo": c}


@router.post("/ciclos")
def criar_ciclo(
    dados: NovoCicloIn,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Dispara um novo ciclo de avaliação para um colaborador.
    Gera tokens únicos e opcionalmente envia e-mail ao colaborador.
    """
    if dados.tipo not in TIPOS_CICLO_VALIDOS:
        raise HTTPException(status_code=400,
            detail=f"Tipo inválido. Válidos: {TIPOS_CICLO_VALIDOS}")
    try:
        datetime.strptime(dados.prazo, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Prazo inválido. Use AAAA-MM-DD.")

    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    colaborador = next((c for c in colaboradores
                        if c["id"] == dados.colaborador_id), None)
    if not colaborador:
        raise HTTPException(status_code=404, detail="Colaborador não encontrado.")
    if not colaborador.get("ativo", True):
        raise HTTPException(status_code=400,
            detail="Não é possível criar ciclo para colaborador desligado.")

    with _lock:
        ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
        # verificar se já há ciclo pendente/em andamento do mesmo tipo para o colaborador
        conflito = next(
            (c for c in ciclos
             if c.get("colaborador_id") == dados.colaborador_id
             and c.get("tipo") == dados.tipo
             and c.get("status") in ("pendente", "em_andamento")),
            None
        )
        if conflito:
            raise HTTPException(status_code=409,
                detail=f"Já existe um ciclo '{dados.tipo}' em andamento para este colaborador (id={conflito['id']}).")

        novo = {
            "id":              _proximo_id(ciclos),
            "colaborador_id":  dados.colaborador_id,
            "tipo":            dados.tipo,
            "prazo":           dados.prazo,
            "status":          "pendente",
            "token_auto":      str(uuid.uuid4()),   # autoavaliação do colaborador
            "token_aval":      str(uuid.uuid4()),   # avaliação do avaliador (Fatia D)
            "token_sup":       str(uuid.uuid4()) if colaborador.get("supervisor_id") else None,
            "email_enviado":   False,
            "email_enviado_em": None,
            "autoavaliacao":          None,   # preenchida na Fatia C (form público)
            "avaliacao_avaliador":    None,   # preenchida na Fatia D
            "avaliacao_supervisor":   None,   # preenchida na Fatia D
            "criado_por":      user["nome"],
            "criado_em":       AGORA(),
            "encerrado_em":    None,
        }
        ciclos.append(novo)
        _escrever_atomico(ciclos, DESEMPENHO_CICLOS_PATH)

    # enviar e-mail fora do lock (SMTP pode ser lento)
    email_ok = False
    if dados.enviar_email:
        email_ok = _enviar_email_autoavaliacao(colaborador, novo)
        if email_ok:
            with _lock:
                ciclos2 = _ler(DESEMPENHO_CICLOS_PATH, [])
                c = next((c for c in ciclos2 if c["id"] == novo["id"]), None)
                if c:
                    c["email_enviado"]    = True
                    c["email_enviado_em"] = AGORA()
                    _escrever_atomico(ciclos2, DESEMPENHO_CICLOS_PATH)
            novo["email_enviado"]    = True
            novo["email_enviado_em"] = AGORA()

    link = f"{_base_url()}/rh/autoavaliacao.html?token={novo['token_auto']}"
    return {
        "ok":          True,
        "ciclo":       novo,
        "link_auto":   link,
        "email_enviado": email_ok,
    }


@router.patch("/ciclos/{cid}/cancelar")
def cancelar_ciclo(
    cid: int,
    user=Depends(exige_papel_rh(["rh_coordenador"]))
):
    """Cancela um ciclo pendente ou em andamento (rh_coordenador)."""
    with _lock:
        ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
        c = next((c for c in ciclos if c["id"] == cid), None)
        if not c:
            raise HTTPException(status_code=404, detail="Ciclo não encontrado.")
        if c.get("status") in ("encerrado", "cancelado"):
            raise HTTPException(status_code=400,
                detail=f"Ciclo já está {c['status']} — não pode ser cancelado.")
        c["status"]        = "cancelado"
        c["cancelado_por"] = user["nome"]
        c["cancelado_em"]  = AGORA()
        _escrever_atomico(ciclos, DESEMPENHO_CICLOS_PATH)
    return {"ok": True, "ciclo": c}


# ---------------------------------------------------------------------------
# ENVIO DE LINKS — avaliador e supervisor (Fatia D)
# ---------------------------------------------------------------------------
def _lookup_usuario(uid: int) -> dict:
    """Retorna dict com nome e email do usuário pelo ID."""
    try:
        todos = auth_store.listar_admin_rh()
        return next((u for u in todos if u.get("id") == uid), {})
    except Exception:
        return {}


@router.post("/ciclos/{cid}/enviar-avaliador")
def enviar_link_avaliador(
    cid: int,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Envia o link de avaliação ao avaliador principal.
    Só disponível após a autoavaliação do colaborador estar preenchida.
    """
    ciclos       = _ler(DESEMPENHO_CICLOS_PATH, [])
    ciclo        = next((c for c in ciclos if c["id"] == cid), None)
    if not ciclo:
        raise HTTPException(status_code=404, detail="Ciclo não encontrado.")
    if not ciclo.get("autoavaliacao"):
        raise HTTPException(status_code=400,
            detail="Aguardando autoavaliação do colaborador antes de enviar ao avaliador.")
    if ciclo.get("avaliacao_avaliador"):
        raise HTTPException(status_code=409,
            detail="O avaliador já preencheu a avaliação deste ciclo.")

    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    colaborador   = next((c for c in colaboradores
                          if c["id"] == ciclo["colaborador_id"]), {})
    avaliador     = _lookup_usuario(colaborador.get("avaliador_id", 0))

    link    = f"{_base_url()}/rh/avaliacao_avaliador.html?token={ciclo['token_aval']}"
    email_ok = _enviar_email_avaliador(
        avaliador.get("email", ""),
        avaliador.get("nome", "Avaliador"),
        colaborador, ciclo
    )
    return {
        "ok":           True,
        "link_aval":    link,
        "email_enviado": email_ok,
        "avaliador":    {"nome": avaliador.get("nome", "–"), "email": avaliador.get("email", "–")},
    }


@router.post("/ciclos/{cid}/enviar-supervisor")
def enviar_link_supervisor(
    cid: int,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Envia o link de co-avaliação ao supervisor de campo.
    Só disponível após a avaliação do avaliador principal estar preenchida.
    """
    ciclos       = _ler(DESEMPENHO_CICLOS_PATH, [])
    ciclo        = next((c for c in ciclos if c["id"] == cid), None)
    if not ciclo:
        raise HTTPException(status_code=404, detail="Ciclo não encontrado.")
    if not ciclo.get("token_sup"):
        raise HTTPException(status_code=400,
            detail="Este colaborador não tem supervisor de campo cadastrado.")
    if not ciclo.get("avaliacao_avaliador"):
        raise HTTPException(status_code=400,
            detail="Aguardando avaliação do avaliador antes de enviar ao supervisor.")
    if ciclo.get("avaliacao_supervisor"):
        raise HTTPException(status_code=409,
            detail="O supervisor já preencheu a co-avaliação deste ciclo.")

    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    colaborador   = next((c for c in colaboradores
                          if c["id"] == ciclo["colaborador_id"]), {})
    supervisor    = _lookup_usuario(colaborador.get("supervisor_id", 0))

    link     = f"{_base_url()}/rh/avaliacao_supervisor.html?token={ciclo['token_sup']}"
    email_ok = _enviar_email_supervisor(
        supervisor.get("email", ""),
        supervisor.get("nome", "Supervisor"),
        colaborador, ciclo
    )
    return {
        "ok":           True,
        "link_sup":     link,
        "email_enviado": email_ok,
        "supervisor":   {"nome": supervisor.get("nome", "–"), "email": supervisor.get("email", "–")},
    }


# ---------------------------------------------------------------------------
# ENCERRAMENTO DO CICLO (Fatia E)
# ---------------------------------------------------------------------------
@router.post("/ciclos/{cid}/encerrar")
def encerrar_ciclo(
    cid: int,
    user=Depends(exige_papel_rh(["rh_coordenador"]))
):
    """Encerra o ciclo, calcula o resultado consolidado e envia e-mail ao colaborador.

    Requisitos mínimos: autoavaliacao + avaliacao_avaliador preenchidas.
    Supervisor é opcional — se o link foi enviado mas não respondido, o ciclo
    pode ser encerrado sem a co-avaliação (resultado calculado só com avaliador).

    Pesos quando há supervisor: avaliador 60% + supervisor 40%.
    Média geral: 50% comportamental + 50% técnica.
    """
    with _lock:
        ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
        ciclo  = next((c for c in ciclos if c["id"] == cid), None)
        if not ciclo:
            raise HTTPException(status_code=404, detail="Ciclo não encontrado.")
        if ciclo.get("status") in ("encerrado", "cancelado"):
            raise HTTPException(status_code=400,
                detail=f"Ciclo já está {ciclo['status']}.")
        if not ciclo.get("autoavaliacao"):
            raise HTTPException(status_code=400,
                detail="Autoavaliação do colaborador ainda não preenchida.")
        if not ciclo.get("avaliacao_avaliador"):
            raise HTTPException(status_code=400,
                detail="Avaliação do avaliador ainda não preenchida.")

        # buscar colaborador e critérios técnicos do setor
        colaboradores   = _ler(DESEMPENHO_COLABORADORES_PATH, [])
        colaborador     = next((c for c in colaboradores
                                if c["id"] == ciclo["colaborador_id"]), {})
        criterios_todos = _ler(DESEMPENHO_CRITERIOS_PATH, [])
        crit_setor      = [c for c in criterios_todos
                           if c.get("setor") == colaborador.get("setor")
                           and c.get("ativo", True)]

        resultado = _calcular_resultado(ciclo, crit_setor)
        resultado["encerrado_por"] = user["nome"]
        resultado["encerrado_em"]  = AGORA()

        ciclo["resultado"] = resultado
        ciclo["status"]    = "encerrado"
        ciclo["encerrado_por"] = user["nome"]
        ciclo["encerrado_em"]  = AGORA()
        _escrever_atomico(ciclos, DESEMPENHO_CICLOS_PATH)

    # enviar e-mail fora do lock
    email_ok = _enviar_email_resultado(colaborador, ciclo, resultado)

    return {
        "ok":             True,
        "ciclo_id":       cid,
        "classificacao":  resultado["classificacao"],
        "media_geral":    resultado["media_geral_final"],
        "email_enviado":  email_ok,
        "resultado":      resultado,
    }


@router.get("/ciclos/{cid}/resultado")
def obter_resultado(
    cid: int,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Retorna o resultado consolidado de um ciclo encerrado."""
    ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
    ciclo  = next((c for c in ciclos if c["id"] == cid), None)
    if not ciclo:
        raise HTTPException(status_code=404, detail="Ciclo não encontrado.")
    if not ciclo.get("resultado"):
        raise HTTPException(status_code=404,
            detail="Ciclo ainda não encerrado — resultado indisponível.")

    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    colaborador   = next((c for c in colaboradores
                          if c["id"] == ciclo["colaborador_id"]), {})
    return {
        "ciclo": {
            "id":         ciclo["id"],
            "tipo":       ciclo["tipo"],
            "tipo_label": TIPOS_CICLO_LABELS.get(ciclo["tipo"], ciclo["tipo"]),
            "prazo":      ciclo.get("prazo",""),
            "encerrado_em": ciclo.get("encerrado_em",""),
        },
        "colaborador": {
            "nome":  colaborador.get("nome","–"),
            "cargo": colaborador.get("cargo","–"),
            "setor": colaborador.get("setor","–"),
        },
        "resultado": ciclo["resultado"],
        "feedback_avaliador": {
            "pontos_fortes":   (ciclo.get("avaliacao_avaliador") or {}).get("pontos_fortes",""),
            "pontos_melhoria": (ciclo.get("avaliacao_avaliador") or {}).get("pontos_melhoria",""),
            "plano_acao":      (ciclo.get("avaliacao_avaliador") or {}).get("plano_acao",""),
        },
    }


# ---------------------------------------------------------------------------
# ROTAS ADMINISTRATIVAS — GATILHOS (Fatia F)
# ---------------------------------------------------------------------------
@router.post("/admin/gatilhos/rodar-agora")
def rodar_gatilhos_agora(
    user=Depends(exige_papel_rh(["rh_coordenador"]))
):
    """Força a verificação e disparo de gatilhos imediatamente (teste / recuperação).
    Útil para validar o comportamento sem esperar a rotina diária.
    """
    resultado = _verificar_e_disparar_gatilhos()
    return {"ok": True, **resultado}


@router.get("/admin/gatilhos/log")
def log_gatilhos(
    limite: int = 50,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Retorna os últimos N disparos automáticos registrados."""
    log = _ler(DESEMPENHO_GATILHOS_LOG_PATH, [])
    log_desc = sorted(log, key=lambda x: x.get("disparado_em",""), reverse=True)
    return {"log": log_desc[:limite], "total": len(log)}


@router.get("/admin/gatilhos/preview")
def preview_gatilhos(
    user=Depends(exige_papel_rh(["rh_coordenador"]))
):
    """Mostra quais ciclos seriam disparados hoje sem realmente criá-los (dry-run)."""
    hoje = date.today()
    preview = []

    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    ciclos        = _ler(DESEMPENHO_CICLOS_PATH, [])
    ativos = [c for c in colaboradores if c.get("ativo", True) and c.get("data_admissao")]

    for colab in ativos:
        try:
            admissao = datetime.strptime(colab["data_admissao"], "%Y-%m-%d").date()
        except ValueError:
            continue
        dias = (hoje - admissao).days

        for tipo, dia_alvo, janela in GATILHOS_EXPERIENCIA:
            if abs(dias - dia_alvo) <= janela:
                ja = _ja_disparado_experiencia(colab["id"], tipo, ciclos)
                preview.append({"colaborador": colab["nome"], "tipo": tipo,
                                 "dia": dias, "dia_alvo": dia_alvo,
                                 "ja_disparado": ja, "acao": "ignorar" if ja else "disparar"})

        if dias >= GATILHO_SEMESTRAL_INICIO_DIA:
            dias_efetivo = dias - GATILHO_SEMESTRAL_INICIO_DIA
            for n in range(1, 30):
                alvo = n * GATILHO_SEMESTRAL_INTERVALO
                if abs(dias_efetivo - alvo) <= GATILHO_SEMESTRAL_JANELA:
                    semestre_key = f"{hoje.year}-S{n}"
                    ja = _ja_disparado_semestral(colab["id"], semestre_key, ciclos)
                    preview.append({"colaborador": colab["nome"], "tipo": "semestral",
                                    "periodo": semestre_key, "dia": dias,
                                    "ja_disparado": ja, "acao": "ignorar" if ja else "disparar"})
                    break

    return {"data_checagem": str(hoje), "preview": preview, "total": len(preview)}


@public_router.get("/autoavaliacao/{token}")
def obter_autoavaliacao_publica(token: str):
    """Retorna os dados do ciclo + critérios para o colaborador preencher.
    Acessado sem login — o token é a única prova de identidade.
    Não expõe tokens internos nem dados de outros colaboradores.
    """
    ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
    ciclo  = next((c for c in ciclos if c.get("token_auto") == token), None)
    if not ciclo:
        raise HTTPException(status_code=404,
            detail="Link inválido ou expirado. Solicite um novo ao RH.")
    if ciclo.get("status") == "cancelado":
        raise HTTPException(status_code=410,
            detail="Este ciclo foi cancelado. Entre em contato com o RH.")
    if ciclo.get("autoavaliacao"):
        raise HTTPException(status_code=409,
            detail="Autoavaliação já preenchida. Obrigado!")

    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    colaborador   = next((c for c in colaboradores
                          if c["id"] == ciclo["colaborador_id"]), None)
    if not colaborador:
        raise HTTPException(status_code=500, detail="Erro interno: colaborador não encontrado.")

    # critérios técnicos do setor do colaborador
    criterios_todos = _ler(DESEMPENHO_CRITERIOS_PATH, [])
    criterios_tecnicos = [
        {"id": str(c["id"]), "descricao": c["descricao"], "aplicavel_a": c.get("aplicavel_a", [])}
        for c in criterios_todos
        if c.get("setor") == colaborador.get("setor") and c.get("ativo", True)
    ]

    return {
        "colaborador": {
            "nome":  colaborador["nome"],
            "cargo": colaborador.get("cargo", ""),
            "setor": colaborador.get("setor", ""),
        },
        "ciclo": {
            "id":         ciclo["id"],
            "tipo":       ciclo["tipo"],
            "tipo_label": TIPOS_CICLO_LABELS.get(ciclo["tipo"], ciclo["tipo"]),
            "prazo":      ciclo.get("prazo", ""),
        },
        "criterios_comportamentais": CRITERIOS_COMPORTAMENTAIS,
        "criterios_tecnicos":        criterios_tecnicos,
        "notas_labels":              NOTAS_LABELS,
    }


@public_router.post("/autoavaliacao/{token}")
def submeter_autoavaliacao(token: str, dados: AutoavaliacaoIn):
    """Recebe e salva a autoavaliação do colaborador.
    Valida notas (1-5) e atualiza o status do ciclo para 'em_andamento'.
    """
    # validar notas
    for item in dados.criterios_comportamentais + dados.criterios_tecnicos:
        nota = item.get("nota") if isinstance(item, dict) else getattr(item, "nota", None)
        if nota not in (1, 2, 3, 4, 5):
            raise HTTPException(status_code=400,
                detail=f"Nota inválida: {nota}. Use valores de 1 a 5.")

    with _lock:
        ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
        ciclo  = next((c for c in ciclos if c.get("token_auto") == token), None)
        if not ciclo:
            raise HTTPException(status_code=404, detail="Link inválido ou expirado.")
        if ciclo.get("status") == "cancelado":
            raise HTTPException(status_code=410, detail="Este ciclo foi cancelado.")
        if ciclo.get("autoavaliacao"):
            raise HTTPException(status_code=409, detail="Autoavaliação já preenchida.")

        ciclo["autoavaliacao"] = {
            "criterios_comportamentais": [
                i if isinstance(i, dict) else i.dict()
                for i in dados.criterios_comportamentais
            ],
            "criterios_tecnicos": [
                i if isinstance(i, dict) else i.dict()
                for i in dados.criterios_tecnicos
            ],
            "observacao_geral": dados.observacao_geral,
            "submetido_em":     AGORA(),
        }
        ciclo["status"] = "em_andamento"   # aguarda avaliador (Fatia D)
        _escrever_atomico(ciclos, DESEMPENHO_CICLOS_PATH)

    return {"ok": True, "mensagem": "Autoavaliação recebida com sucesso. Obrigado!"}


# ---------------------------------------------------------------------------
# ROTAS PÚBLICAS — AVALIADOR (Fatia D)
# ---------------------------------------------------------------------------
@public_router.get("/avaliacao/{token}")
def obter_avaliacao_publica(token: str):
    """Retorna dados do ciclo + autoavaliação do colaborador + critérios
    para o avaliador preencher. Não expõe token_auto nem token_sup.
    """
    ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
    ciclo  = next((c for c in ciclos if c.get("token_aval") == token), None)
    if not ciclo:
        raise HTTPException(status_code=404, detail="Link inválido ou expirado.")
    if ciclo.get("status") == "cancelado":
        raise HTTPException(status_code=410, detail="Este ciclo foi cancelado.")
    if ciclo.get("avaliacao_avaliador"):
        raise HTTPException(status_code=409, detail="Avaliação já preenchida. Obrigado!")
    if not ciclo.get("autoavaliacao"):
        raise HTTPException(status_code=400,
            detail="O colaborador ainda não preencheu a autoavaliação.")

    colaboradores   = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    colaborador     = next((c for c in colaboradores
                            if c["id"] == ciclo["colaborador_id"]), None)
    if not colaborador:
        raise HTTPException(status_code=500, detail="Erro interno.")

    criterios_todos    = _ler(DESEMPENHO_CRITERIOS_PATH, [])
    criterios_tecnicos = [
        {"id": str(c["id"]), "descricao": c["descricao"], "aplicavel_a": c.get("aplicavel_a", [])}
        for c in criterios_todos
        if c.get("setor") == colaborador.get("setor") and c.get("ativo", True)
    ]

    return {
        "colaborador": {
            "nome":  colaborador["nome"],
            "cargo": colaborador.get("cargo", ""),
            "setor": colaborador.get("setor", ""),
        },
        "ciclo": {
            "id":         ciclo["id"],
            "tipo":       ciclo["tipo"],
            "tipo_label": TIPOS_CICLO_LABELS.get(ciclo["tipo"], ciclo["tipo"]),
            "prazo":      ciclo.get("prazo", ""),
        },
        "autoavaliacao":             ciclo["autoavaliacao"],
        "criterios_comportamentais": CRITERIOS_COMPORTAMENTAIS,
        "criterios_tecnicos":        criterios_tecnicos,
        "notas_labels":              NOTAS_LABELS,
    }


@public_router.post("/avaliacao/{token}")
def submeter_avaliacao_avaliador(token: str, dados: AvaliacaoAvaliadorIn):
    """Salva a avaliação do avaliador principal."""
    for item in dados.criterios_comportamentais + dados.criterios_tecnicos:
        nota = item.get("nota") if isinstance(item, dict) else getattr(item, "nota", None)
        if nota not in (1, 2, 3, 4, 5):
            raise HTTPException(status_code=400, detail=f"Nota inválida: {nota}. Use 1-5.")

    with _lock:
        ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
        ciclo  = next((c for c in ciclos if c.get("token_aval") == token), None)
        if not ciclo:
            raise HTTPException(status_code=404, detail="Link inválido ou expirado.")
        if ciclo.get("avaliacao_avaliador"):
            raise HTTPException(status_code=409, detail="Avaliação já preenchida.")

        ciclo["avaliacao_avaliador"] = {
            "criterios_comportamentais": [
                i if isinstance(i, dict) else i.dict()
                for i in dados.criterios_comportamentais
            ],
            "criterios_tecnicos": [
                i if isinstance(i, dict) else i.dict()
                for i in dados.criterios_tecnicos
            ],
            "pontos_fortes":   dados.pontos_fortes,
            "pontos_melhoria": dados.pontos_melhoria,
            "plano_acao":      dados.plano_acao,
            "submetido_em":    AGORA(),
        }
        _escrever_atomico(ciclos, DESEMPENHO_CICLOS_PATH)
    return {"ok": True, "mensagem": "Avaliação registrada com sucesso. Obrigado!"}


# ---------------------------------------------------------------------------
# ROTAS PÚBLICAS — SUPERVISOR (Fatia D)
# ---------------------------------------------------------------------------
@public_router.get("/supervisor/{token}")
def obter_coavaliacao_publica(token: str):
    """Retorna dados do ciclo + autoavaliação + avaliação do avaliador
    para o supervisor de campo preencher a co-avaliação.
    """
    ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
    ciclo  = next((c for c in ciclos if c.get("token_sup") == token), None)
    if not ciclo:
        raise HTTPException(status_code=404, detail="Link inválido ou expirado.")
    if ciclo.get("status") == "cancelado":
        raise HTTPException(status_code=410, detail="Este ciclo foi cancelado.")
    if ciclo.get("avaliacao_supervisor"):
        raise HTTPException(status_code=409, detail="Co-avaliação já preenchida. Obrigado!")
    if not ciclo.get("avaliacao_avaliador"):
        raise HTTPException(status_code=400,
            detail="Aguardando avaliação do responsável antes da co-avaliação.")

    colaboradores   = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    colaborador     = next((c for c in colaboradores
                            if c["id"] == ciclo["colaborador_id"]), None)
    if not colaborador:
        raise HTTPException(status_code=500, detail="Erro interno.")

    criterios_todos    = _ler(DESEMPENHO_CRITERIOS_PATH, [])
    criterios_tecnicos = [
        {"id": str(c["id"]), "descricao": c["descricao"]}
        for c in criterios_todos
        if c.get("setor") == colaborador.get("setor") and c.get("ativo", True)
    ]

    return {
        "colaborador": {
            "nome":  colaborador["nome"],
            "cargo": colaborador.get("cargo", ""),
            "setor": colaborador.get("setor", ""),
        },
        "ciclo": {
            "id":         ciclo["id"],
            "tipo":       ciclo["tipo"],
            "tipo_label": TIPOS_CICLO_LABELS.get(ciclo["tipo"], ciclo["tipo"]),
            "prazo":      ciclo.get("prazo", ""),
        },
        "autoavaliacao":             ciclo["autoavaliacao"],
        "avaliacao_avaliador":       ciclo["avaliacao_avaliador"],
        "criterios_comportamentais": CRITERIOS_COMPORTAMENTAIS,
        "criterios_tecnicos":        criterios_tecnicos,
        "notas_labels":              NOTAS_LABELS,
    }


@public_router.post("/supervisor/{token}")
def submeter_coavaliacao_supervisor(token: str, dados: AvaliacaoSupervisorIn):
    """Salva a co-avaliação do supervisor de campo."""
    for item in dados.criterios_comportamentais + dados.criterios_tecnicos:
        nota = item.get("nota") if isinstance(item, dict) else getattr(item, "nota", None)
        if nota not in (1, 2, 3, 4, 5):
            raise HTTPException(status_code=400, detail=f"Nota inválida: {nota}. Use 1-5.")

    with _lock:
        ciclos = _ler(DESEMPENHO_CICLOS_PATH, [])
        ciclo  = next((c for c in ciclos if c.get("token_sup") == token), None)
        if not ciclo:
            raise HTTPException(status_code=404, detail="Link inválido ou expirado.")
        if ciclo.get("avaliacao_supervisor"):
            raise HTTPException(status_code=409, detail="Co-avaliação já preenchida.")

        ciclo["avaliacao_supervisor"] = {
            "criterios_comportamentais": [
                i if isinstance(i, dict) else i.dict()
                for i in dados.criterios_comportamentais
            ],
            "criterios_tecnicos": [
                i if isinstance(i, dict) else i.dict()
                for i in dados.criterios_tecnicos
            ],
            "observacao_geral": dados.observacao_geral,
            "submetido_em":     AGORA(),
        }
        _escrever_atomico(ciclos, DESEMPENHO_CICLOS_PATH)
    return {"ok": True, "mensagem": "Co-avaliação registrada com sucesso. Obrigado!"}


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
    with _lock_colab:
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


# ---------------------------------------------------------------------------
# PAINEL DE ACOMPANHAMENTO (Fatia G)
# ---------------------------------------------------------------------------
def _proximo_gatilho(colab: dict, ciclos_colab: list) -> dict | None:
    """Calcula o próximo gatilho automático previsto para o colaborador."""
    if not colab.get("data_admissao"):
        return None
    try:
        admissao = datetime.strptime(colab["data_admissao"], "%Y-%m-%d").date()
    except ValueError:
        return None

    hoje = date.today()
    dias = (hoje - admissao).days

    tipos_nao_cancelados = {c["tipo"] for c in ciclos_colab if c.get("status") != "cancelado"}

    # Experiência 1
    if "experiencia_1" not in tipos_nao_cancelados and dias < 39:
        data_prev = admissao + timedelta(days=35)
        return {"tipo": "experiencia_1", "tipo_label": "Exp. 35º dia",
                "data_prevista": str(data_prev), "dias_restantes": (data_prev - hoje).days}

    # Experiência 2
    if "experiencia_2" not in tipos_nao_cancelados and dias < 84:
        data_prev = admissao + timedelta(days=80)
        return {"tipo": "experiencia_2", "tipo_label": "Exp. 80º dia",
                "data_prevista": str(data_prev), "dias_restantes": (data_prev - hoje).days}

    # Próximo semestral
    if dias >= GATILHO_SEMESTRAL_INICIO_DIA:
        dias_efetivo = dias - GATILHO_SEMESTRAL_INICIO_DIA
        for n in range(1, 30):
            alvo = n * GATILHO_SEMESTRAL_INTERVALO
            semestre_key = f"{hoje.year}-S{n}"
            # já existe semestral deste período?
            if any(c.get("tipo") == "semestral" and c.get("periodo_ref") == semestre_key
                   and c.get("status") != "cancelado" for c in ciclos_colab):
                continue
            if alvo > dias_efetivo:
                data_prev = admissao + timedelta(days=GATILHO_SEMESTRAL_INICIO_DIA + alvo)
                return {"tipo": "semestral", "tipo_label": f"Semestral ({semestre_key})",
                        "data_prevista": str(data_prev),
                        "dias_restantes": (data_prev - hoje).days}
    return None


@router.get("/acompanhamento")
def painel_acompanhamento(
    setor: Optional[str] = None,
    user=Depends(exige_papel_rh(["rh_operador", "rh_coordenador"]))
):
    """Painel consolidado: visão geral de todos os colaboradores com histórico
    de ciclos, médias, alertas e próximos gatilhos previstos.
    """
    hoje = date.today()
    doze_meses_atras = str(hoje.replace(year=hoje.year - 1))

    colaboradores = _ler(DESEMPENHO_COLABORADORES_PATH, [])
    ciclos_todos  = _ler(DESEMPENHO_CICLOS_PATH, [])

    # lookup de usuários para nomes de avaliadores
    try:
        usuarios_map = {u["id"]: u["nome"] for u in auth_store.listar_admin_rh()}
    except Exception:
        usuarios_map = {}

    ativos = [c for c in colaboradores if c.get("ativo", True)]
    if setor:
        ativos = [c for c in ativos if (c.get("setor") or "").lower() == setor.lower()]

    # índice de ciclos por colaborador
    ciclos_por_colab: dict[int, list] = {}
    for ciclo in ciclos_todos:
        cid = ciclo.get("colaborador_id")
        if cid not in ciclos_por_colab:
            ciclos_por_colab[cid] = []
        ciclos_por_colab[cid].append(ciclo)

    # medias para KPI empresa (últimos 12 meses, só encerrados)
    medias_empresa = []
    for ciclo in ciclos_todos:
        if ciclo.get("status") != "encerrado":
            continue
        enc_em = (ciclo.get("encerrado_em") or "")[:10]
        if enc_em < doze_meses_atras:
            continue
        m = (ciclo.get("resultado") or {}).get("media_geral_final")
        if m is not None:
            medias_empresa.append(m)

    media_empresa = round(sum(medias_empresa)/len(medias_empresa), 2) if medias_empresa else None

    linhas = []
    sem_ciclo_ativo = 0
    ciclos_abertos  = 0

    for colab in ativos:
        colab_id    = colab["id"]
        ciclos_colab = ciclos_por_colab.get(colab_id, [])
        ciclos_nc   = [c for c in ciclos_colab if c.get("status") != "cancelado"]

        # ciclo ativo (pendente ou em andamento)
        ciclo_ativo = next(
            (c for c in sorted(ciclos_nc, key=lambda x: x.get("criado_em",""), reverse=True)
             if c.get("status") in ("pendente","em_andamento")), None)

        # último ciclo encerrado
        encerrados = sorted(
            [c for c in ciclos_nc if c.get("status") == "encerrado"],
            key=lambda x: x.get("encerrado_em",""), reverse=True)
        ultimo_enc = encerrados[0] if encerrados else None

        # médias encerrados dos últimos 12 meses
        medias_colab = [
            (c.get("resultado") or {}).get("media_geral_final")
            for c in encerrados
            if (c.get("encerrado_em",""))[:10] >= doze_meses_atras
            and (c.get("resultado") or {}).get("media_geral_final") is not None
        ]
        media_historica = round(sum(medias_colab)/len(medias_colab), 2) if medias_colab else None

        # alertas
        if not ciclo_ativo:
            sem_ciclo_ativo += 1
        else:
            ciclos_abertos += 1

        # histórico resumido (últimos 5 ciclos não-cancelados)
        historico = []
        for c in sorted(ciclos_nc, key=lambda x: x.get("criado_em",""), reverse=True)[:5]:
            historico.append({
                "id":           c["id"],
                "tipo":         c["tipo"],
                "status":       c["status"],
                "media_geral":  (c.get("resultado") or {}).get("media_geral_final"),
                "classificacao": (c.get("resultado") or {}).get("classificacao"),
                "criado_em":    c.get("criado_em","")[:10],
                "encerrado_em": (c.get("encerrado_em") or "")[:10] or None,
            })

        proximo = _proximo_gatilho(colab, ciclos_colab)

        linhas.append({
            "id":              colab_id,
            "nome":            colab["nome"],
            "cargo":           colab.get("cargo",""),
            "setor":           colab.get("setor",""),
            "data_admissao":   colab.get("data_admissao",""),
            "avaliador_nome":  usuarios_map.get(colab.get("avaliador_id",""), "–"),
            "ciclo_ativo":     ciclo_ativo and {
                "id":     ciclo_ativo["id"],
                "tipo":   ciclo_ativo["tipo"],
                "tipo_label": TIPOS_CICLO_LABELS.get(ciclo_ativo["tipo"], ciclo_ativo["tipo"]),
                "status": ciclo_ativo["status"],
                "prazo":  ciclo_ativo.get("prazo",""),
                "etapa_auto": bool(ciclo_ativo.get("autoavaliacao")),
                "etapa_aval": bool(ciclo_ativo.get("avaliacao_avaliador")),
            },
            "ultimo_encerrado": ultimo_enc and {
                "id":            ultimo_enc["id"],
                "tipo_label":    TIPOS_CICLO_LABELS.get(ultimo_enc["tipo"], ultimo_enc["tipo"]),
                "media_geral":   (ultimo_enc.get("resultado") or {}).get("media_geral_final"),
                "classificacao": (ultimo_enc.get("resultado") or {}).get("classificacao"),
                "encerrado_em":  (ultimo_enc.get("encerrado_em") or "")[:10],
            },
            "media_historica":  media_historica,
            "ciclos_total":     len(ciclos_nc),
            "ciclos_encerrados": len(encerrados),
            "historico":        historico,
            "proximo_gatilho":  proximo,
        })

    # ordenar: com ciclo ativo primeiro, depois por nome
    linhas.sort(key=lambda x: (0 if x["ciclo_ativo"] else 1, x["nome"].lower()))

    # médias por setor (apenas encerrados)
    setores_map: dict[str, list] = {}
    for colab in ativos:
        s = colab.get("setor","Outros")
        ciclos_colab = ciclos_por_colab.get(colab["id"], [])
        for c in ciclos_colab:
            if c.get("status") != "encerrado":
                continue
            m = (c.get("resultado") or {}).get("media_geral_final")
            if m is not None:
                if s not in setores_map:
                    setores_map[s] = []
                setores_map[s].append(m)

    por_setor = sorted([
        {"setor": s, "media": round(sum(v)/len(v),2),
         "ciclos_encerrados": len(v),
         "classificacao": _classificar(round(sum(v)/len(v),2))}
        for s, v in setores_map.items()
    ], key=lambda x: x["media"], reverse=True)

    return {
        "kpis": {
            "colaboradores_ativos":    len(ativos),
            "ciclos_abertos":          ciclos_abertos,
            "sem_ciclo_ativo":         sem_ciclo_ativo,
            "media_empresa_12m":       media_empresa,
            "classificacao_empresa":   _classificar(media_empresa) if media_empresa else "–",
            "ciclos_encerrados_12m":   len(medias_empresa),
        },
        "colaboradores": linhas,
        "por_setor":     por_setor,
    }


# ---------------------------------------------------------------------------
# THREAD DE GATILHOS — inicia na última linha do módulo, de propósito.
# Evita corrida com nomes definidos mais acima.
# Daemon=True: encerra junto com o processo principal.
# ---------------------------------------------------------------------------
_thread_gatilhos = threading.Thread(
    target=_rotina_gatilhos_diaria,
    name="gatilhos-desempenho",
    daemon=True,
)
_thread_gatilhos.start()
