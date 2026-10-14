#!/usr/bin/env python3
"""
Caminho A — Gerador de dados vivos do Dashboard Comercial
=========================================================
Lê a planilha comercial mais recente da pasta Comercial e gera
o arquivo data/comercial.json que o dashboard consome.

Uso:
    python3 gerar_json.py

Sem argumentos: usa os caminhos padrão do container (ver CONFIG abaixo).
Pode sobrescrever por variáveis de ambiente:
    PASTA_COMERCIAL=/caminho/para/Comercial
    SAIDA_JSON=/caminho/para/data/comercial.json

Regras de negócio (definidas com o comercial):
  - Recorrente ativo   = soma MENSALIDADE onde STATUS = "Ativo"
  - Obras faturadas    = soma VALOR OBRA no PIPELINE onde estágio = "Fechado / Ganho" E há valor
  - Pipeline exibido   = todas as linhas do PIPELINE, exceto estágio "Perdido"
  - Recorrente ponder. = Σ(RECORRENTE EST. × PROB.) das linhas nem ganhas nem perdidas
  - Licitações         = todas + contagem por status + somatórios por status
  - Prospecção         = todos os leads preenchidos
  - Contratos/Histórico= direto das abas
"""

import os
import sys
import json
import glob
import unicodedata
from datetime import datetime, date
from pathlib import Path

import openpyxl

# ------------------------------------------------------------------ CONFIG
# Caminhos padrão pensados para rodar DENTRO do container da intranet.
# O volume da pasta Comercial é montado em /data/comercial (read-only).
# O JSON é gravado em data/ (mesma pasta que o main.py serve via /api/dados).
PASTA_COMERCIAL = os.environ.get("PASTA_COMERCIAL", "/data/comercial")
SAIDA_JSON = os.environ.get(
    "SAIDA_JSON", str(Path(__file__).parent / "data" / "comercial.json")
)

# Metas (fixas, aprovadas pela diretoria). Se mudarem, editar aqui.
META_RECORRENTE_MENSAL = 60000.0
META_OBRAS_ANUAL = 2400000.0


# ------------------------------------------------------------------ HELPERS
def _norm(texto):
    """Normaliza texto p/ comparação: minúsculo, sem acento, sem espaço extra."""
    if texto is None:
        return ""
    t = str(texto).strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t


def _num(v):
    """Converte para float de forma tolerante. Vazio/texto -> None."""
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _txt(v):
    """Texto limpo para exibição. None -> string vazia."""
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.strftime("%d/%m/%Y")
    return str(v).strip()


def _brl(valor):
    """Formata número como moeda BR: 42041.41 -> 'R$ 42.041'."""
    if valor is None:
        return "—"
    return "R$ " + f"{valor:,.0f}".replace(",", ".")


def _grupo_cliente(nome):
    """Agrupa contratos por 'família' de cliente para medir concentração.
    A Light aparece em vários contratos com nomes ligeiramente diferentes
    (1 Equipe, Manutenção, etc.); todos contam como um único cliente-grupo.
    Demais clientes ficam pelo próprio nome (limpo)."""
    n = _norm(nome)
    if "light" in n:
        return "Light"
    if "senac" in n:
        return "Senac"
    if "creche" in n or "criando com arte" in n:
        return "Creche Criando com Arte"
    # fallback: nome original enxuto (sem o que vem após um separador)
    base = _txt(nome).split("—")[0].split("-")[0].strip()
    return base or _txt(nome)


def achar_planilha(pasta):
    """Retorna o .xlsx comercial_safenet* mais recente da pasta."""
    padrao = os.path.join(pasta, "comercial_safenet*.xlsx")
    arquivos = [a for a in glob.glob(padrao) if not os.path.basename(a).startswith("~$")]
    if not arquivos:
        # fallback: qualquer .xlsx na pasta
        arquivos = [
            a for a in glob.glob(os.path.join(pasta, "*.xlsx"))
            if not os.path.basename(a).startswith("~$")
        ]
    if not arquivos:
        raise FileNotFoundError(
            f"Nenhuma planilha .xlsx encontrada em {pasta}. "
            "Confira se o volume da pasta Comercial está montado."
        )
    # mais recente por data de modificação
    return max(arquivos, key=os.path.getmtime)


def carregar_aba(ws):
    """Retorna (cabecalhos, linhas) de uma aba, achando a linha de header (# ou ITEM)."""
    rows = list(ws.iter_rows(values_only=True))
    hdr_idx = None
    for i, r in enumerate(rows):
        if r and r[0] in ("#", "ITEM"):
            hdr_idx = i
            break
    if hdr_idx is None:
        return [], []
    hdr = list(rows[hdr_idx])
    linhas = [r for r in rows[hdr_idx + 1:] if r and r[0] is not None]
    return hdr, linhas


def col(hdr, *nomes):
    """Índice da 1ª coluna cujo cabeçalho casa (normalizado) com algum dos nomes."""
    alvo = [_norm(n) for n in nomes]
    for j, h in enumerate(hdr):
        hn = _norm(h)
        if any(a in hn for a in alvo):
            return j
    return None


# ------------------------------------------------------------------ EXTRAÇÃO
def extrair(wb):
    dados = {}

    # ---------------- CONTRATOS ATIVOS -> recorrente + lista
    hdr, linhas = carregar_aba(wb["CONTRATOS ATIVOS"])
    c_cli = col(hdr, "cliente")
    c_tipo = col(hdr, "tipo de servico", "tipo")
    c_mens = col(hdr, "mensalidade")
    c_st = col(hdr, "status")
    c_venc = col(hdr, "vencimento")

    recorrente_ativo = 0.0
    contratos = []
    rec_por_grupo = {}   # grupo de cliente -> recorrente somado (p/ concentração)
    for r in linhas:
        st = _norm(r[c_st]) if c_st is not None else ""
        mens = _num(r[c_mens]) if c_mens is not None else None
        if "ativo" in st and mens:
            recorrente_ativo += mens
            nome_cli = _txt(r[c_cli])
            grupo = _grupo_cliente(nome_cli)
            rec_por_grupo[grupo] = rec_por_grupo.get(grupo, 0.0) + mens
            contratos.append({
                "l": nome_cli,
                "d": _txt(r[c_tipo]) if c_tipo is not None else "",
                "v": (_brl(mens) + "/m"),
            })

    # ---------------- PIPELINE -> pipeline, obras faturadas, ponderado, funil
    hdr, linhas = carregar_aba(wb["PIPELINE"])
    p_cli = col(hdr, "cliente")
    p_est = col(hdr, "estagio")
    p_cont = col(hdr, "contato")
    p_rec = col(hdr, "recorrente est")
    p_prob = col(hdr, "prob")
    p_val = col(hdr, "valor obra")
    p_obs = col(hdr, "proximo passo", "observacao")
    p_resp = col(hdr, "responsavel")

    # mapa estágio -> classe/rótulo usados pelo dashboard (pill)
    MAPA_EST = {
        "fechado / ganho": ("ganho", "Ganho ✅"),
        "proposta enviada": ("enviada", "Enviada"),
        "em andamento": ("andamento", "Em andamento"),
        "em negociacao": ("negociacao", "Negociação"),
        "prospeccao": ("prospeccao", "Prospecção"),
        "perdido": ("perdido", "Perdido"),
    }

    pipeline = []
    obras_faturadas = 0.0
    obras_ganhas = []           # lista de obras já aprovadas (p/ nota dinâmica)
    obras_ponderadas = 0.0      # Σ(valor obra × prob) das linhas em aberto (sug. 2)
    recorrente_ponderado = 0.0
    pipe_sem_prob = 0           # linhas em aberto sem PROB. preenchida (sug. 3)
    pipe_sem_rec = 0            # linhas em aberto sem RECORRENTE EST. (sug. 3)
    pipe_valor_sem_prob = 0.0   # valor de obra "cego" por falta de prob. (sug. 3)
    funil = {"prospeccao": 0, "enviada": 0, "andamento": 0,
             "negociacao": 0, "ganho": 0, "perdido": 0}

    for r in linhas:
        est_norm = _norm(r[p_est]) if p_est is not None else ""
        if not est_norm:
            continue
        classe, rotulo = MAPA_EST.get(est_norm, ("prospeccao", _txt(r[p_est])))

        # regra: pipeline exibido = tudo menos "perdido"
        if classe == "perdido":
            funil["perdido"] += 1
            continue

        val = _num(r[p_val]) if p_val is not None else None
        prob = _num(r[p_prob]) if p_prob is not None else None
        rec = _num(r[p_rec]) if p_rec is not None else None

        # obras faturadas: ganho + tem valor
        if classe == "ganho" and val:
            obras_faturadas += val
            obras_ganhas.append({"n": _txt(r[p_cli]), "v": _brl(val)})

        # recorrente ponderado: nem ganho nem perdido, com rec e prob
        if classe not in ("ganho",) and rec and prob is not None:
            recorrente_ponderado += rec * prob

        # --- linhas em aberto (nem ganho nem perdido): sugestões 2 e 3 ---
        if classe not in ("ganho",):
            # obras projetadas: soma valor da obra ponderado pela probabilidade
            if val and prob is not None:
                obras_ponderadas += val * prob
            # dados faltando que distorcem os ponderados
            if prob is None:
                pipe_sem_prob += 1
                if val:
                    pipe_valor_sem_prob += val   # obra grande "invisível" no ponderado
            if not rec:
                pipe_sem_rec += 1

        # funil
        if classe in funil:
            funil[classe] += 1

        # texto do card (cru da planilha — obs curada só se existir)
        contato = _txt(r[p_cont]) if p_cont is not None else ""
        resp = _txt(r[p_resp]) if p_resp is not None else ""
        obs = _txt(r[p_obs]) if p_obs is not None else ""
        desc_partes = [x for x in (resp or contato, obs) if x]
        desc = " · ".join(desc_partes)[:180]

        item = {
            "n": _txt(r[p_cli]),
            "c": desc,
            "v": _brl(val) if val else ("—" if rec else "—"),
            "s": classe,
            "sl": rotulo,
            "p": (round(prob * 100, 1) if prob is not None else None),
        }
        # marca linhas "Light" (destaque visual no dashboard)
        if "light" in _norm(r[p_cli]):
            item["light"] = 1
        pipeline.append(item)

    pipeline_ativo = len(pipeline)

    # ---------------- LICITAÇÕES -> tabela + kpis + somatórios por status
    hdr, linhas = carregar_aba(wb["LICITAÇÕES"])
    l_item = col(hdr, "item")
    l_org = col(hdr, "orgao")
    l_ed = col(hdr, "edital")
    l_ab = col(hdr, "data abertura", "abertura")
    l_lim = col(hdr, "limite de entrega", "limite")
    l_ex = col(hdr, "executor")
    l_val = col(hdr, "valor estimado")
    l_st = col(hdr, "status")

    # Fluxo oficial (validação de dados da planilha):
    #   Não iniciado -> Em elaboração -> Proposta enviada — Licitação
    #   -> Em pregão (aguardando sessão) -> Habilitado -> Ganho / Perdido
    # Obs: "ganho" e "habilitado" são estados DIFERENTES. Habilitado ainda
    # não é ganho — misturar os dois inflaria o valor ganho.
    MAPA_LIC = {
        "em pregao (aguardando sessao)": ("emprego", "Em pregão"),
        "em elaboracao": ("elaboracao", "Em elaboração"),
        "proposta enviada — licitacao": ("propostaenv", "Proposta enviada"),
        "nao iniciado": ("naoiniciado", "Não iniciado"),
        "nao participada": ("naopart", "Não participada"),
        "habilitado": ("habilitado", "Habilitado"),
        "ganho": ("ganho", "Ganho"),
        "perdido": ("perdido", "Perdido"),
    }

    # Status que compõem "em andamento" (aprovado com o comercial):
    # em elaboração + proposta enviada + em pregão + habilitado.
    # Ficam de fora: não iniciado, não participada, ganho, perdido.
    LIC_ANDAMENTO = {"elaboracao", "propostaenv", "emprego", "habilitado"}

    licitacoes = []
    lic_cont = {"emprego": 0, "elaboracao": 0, "propostaenv": 0,
                "naoiniciado": 0, "naopart": 0, "habilitado": 0,
                "ganho": 0, "perdido": 0}
    lic_valor = {"andamento": 0.0, "naopart": 0.0}
    lic_and_qtd = 0        # linhas em andamento
    lic_and_sem_valor = 0  # dessas, quantas sem VALOR ESTIMADO
    lic_st_desconhecidos = set()

    def _mmdd(v):
        t = _txt(v)
        # dd/mm/aaaa -> dd/mm
        if len(t) >= 5 and t[2] == "/":
            return t[:5]
        return t

    for r in linhas:
        st_norm = _norm(r[l_st]) if l_st is not None else ""
        if st_norm in MAPA_LIC:
            classe, rotulo = MAPA_LIC[st_norm]
        else:
            # status fora do fluxo conhecido: não inventa classe, só avisa
            classe, rotulo = ("naoiniciado", _txt(r[l_st]))
            if st_norm:
                lic_st_desconhecidos.add(_txt(r[l_st]))
        if classe in lic_cont:
            lic_cont[classe] += 1

        val = _num(r[l_val]) if l_val is not None else None

        # valor em andamento: soma o VALOR ESTIMADO das linhas em aberto
        if classe in LIC_ANDAMENTO:
            lic_and_qtd += 1
            if val:
                lic_valor["andamento"] += val
            else:
                lic_and_sem_valor += 1
        elif classe == "naopart" and val:
            lic_valor["naopart"] += val

        licitacoes.append({
            "i": r[l_item] if l_item is not None else "",
            "o": _txt(r[l_org]),
            "e": _txt(r[l_ed]) if l_ed is not None else "",
            "ab": _mmdd(r[l_ab]) if l_ab is not None else "",
            "lim": _mmdd(r[l_lim]) if l_lim is not None else "",
            "ex": _txt(r[l_ex]) if l_ex is not None else "",
            "s": classe,
            "sl": rotulo,
            "v": _brl(val) if val else "—",
        })

    if lic_st_desconhecidos:
        print(f"[aviso] STATUS de licitação fora do fluxo conhecido: "
              f"{sorted(lic_st_desconhecidos)}", file=sys.stderr)

    # ---------------- HISTÓRICO (licitações) -> ganho / perdido
    # Licitação encerrada sai da aba LICITAÇÕES e vira linha no HISTÓRICO.
    # Por isso ganho/perdido de licitação SÓ podem ser lidos daqui,
    # filtrando ORIGEM == "Licitação".
    hdr_h, linhas_h = carregar_aba(wb["HISTÓRICO"])
    hh_org = col(hdr_h, "origem")
    hh_res = col(hdr_h, "resultado")
    hh_val = col(hdr_h, "valor estimado")

    lic_ganho = lic_perdido = 0.0
    lic_qtd_ganho = lic_qtd_perdido = 0

    if hh_org is not None and hh_res is not None and hh_val is not None:
        for r in linhas_h:
            if _norm(r[hh_org]) != "licitacao":
                continue
            res = _norm(r[hh_res])
            val = _num(r[hh_val])
            if res == "ganho":
                lic_qtd_ganho += 1
                if val:
                    lic_ganho += val
            elif res.startswith("perdido"):
                lic_qtd_perdido += 1
                if val:
                    lic_perdido += val
    else:
        print("[aviso] aba HISTÓRICO sem colunas ORIGEM/RESULTADO/VALOR ESTIMADO — "
              "valor_ganho e valor_perdido ficarão zerados.", file=sys.stderr)

    # ---------------- PROSPECÇÃO -> todos os leads preenchidos
    hdr, linhas = carregar_aba(wb["PROSPECÇÃO"])
    pr_emp = col(hdr, "empresa", "lead")
    pr_seg = col(hdr, "segmento")
    pr_st = col(hdr, "status")
    prospec = []
    for r in linhas:
        emp = _txt(r[pr_emp]) if pr_emp is not None else ""
        if not emp:
            continue
        seg = _txt(r[pr_seg]) if pr_seg is not None else ""
        st = _txt(r[pr_st]) if pr_st is not None else ""
        d = " · ".join([x for x in (seg, st.lower()) if x])
        prospec.append({"l": emp, "d": d})

    # ---------------- HISTÓRICO -> encerrados
    hdr, linhas = carregar_aba(wb["HISTÓRICO"])
    h_cli = col(hdr, "cliente")
    h_res = col(hdr, "resultado")
    # a coluna passou a se chamar "VALOR ESTIMADO (R$)"; "valor obra" fica
    # como fallback para planilhas antigas.
    h_val = col(hdr, "valor estimado", "valor obra")
    h_org = col(hdr, "origem")

    # análise de conversão por origem + motivos de perda (sugestão 5).
    # base pequena hoje (~6 encerradas): expomos 'amostra' p/ o dashboard
    # exibir com a devida humildade estatística.
    conv = {}   # origem -> {"ganho": n, "perdido": n}
    motivos_perda = {}   # rótulo curto do resultado -> contagem
    amostra_hist = 0

    historico = []
    for r in linhas:
        cli = _txt(r[h_cli]) if h_cli is not None else ""
        if not cli:
            continue
        val = _num(r[h_val]) if h_val is not None else None
        res_txt = _txt(r[h_res]) if h_res is not None else ""
        res_norm = _norm(res_txt)
        org_txt = _txt(r[h_org]) if h_org is not None else ""

        # tabula conversão e motivos
        amostra_hist += 1
        og_key = org_txt or "—"
        conv.setdefault(og_key, {"ganho": 0, "perdido": 0})
        if res_norm == "ganho":
            conv[og_key]["ganho"] += 1
        elif res_norm.startswith("perdido"):
            conv[og_key]["perdido"] += 1
            # motivo = o que vem depois de "perdido —" (ex.: "preço", "sem resposta")
            motivo = res_txt.split("—", 1)[1].strip() if "—" in res_txt else "não especificado"
            motivos_perda[motivo] = motivos_perda.get(motivo, 0) + 1

        item_h = {
            "l": cli,
            "d": res_txt,
            "v": _brl(val) if val else "—",
        }
        if h_org is not None:
            item_h["og"] = org_txt   # "Licitação" | "Privada"
        historico.append(item_h)

    # monta lista de conversão por origem (com taxa quando houver base)
    conversao_origem = []
    for og, c in sorted(conv.items()):
        encerr = c["ganho"] + c["perdido"]
        taxa = round(100 * c["ganho"] / encerr, 1) if encerr else None
        conversao_origem.append({
            "origem": og,
            "ganho": c["ganho"],
            "perdido": c["perdido"],
            "encerradas": encerr,
            "taxa_ganho": taxa,
        })
    # motivos de perda ordenados do mais comum ao menos
    motivos_ordenados = sorted(motivos_perda.items(), key=lambda kv: -kv[1])
    motivos_perda_lista = [{"motivo": m, "q": q} for m, q in motivos_ordenados]

    # ---------------- KPIs derivados
    pct_rec = recorrente_ativo / META_RECORRENTE_MENSAL if META_RECORRENTE_MENSAL else 0
    pct_obras = obras_faturadas / META_OBRAS_ANUAL if META_OBRAS_ANUAL else 0

    # concentração de cliente (sug. 1): maior grupo sobre o recorrente total
    maior_grupo, maior_grupo_valor = ("—", 0.0)
    if rec_por_grupo:
        maior_grupo, maior_grupo_valor = max(rec_por_grupo.items(), key=lambda kv: kv[1])
    pct_concentracao = (maior_grupo_valor / recorrente_ativo * 100) if recorrente_ativo else 0

    # meta de obras projetada (sug. 2): faturado + pipeline ponderado por prob.
    obras_projetadas = obras_faturadas + obras_ponderadas
    pct_obras_proj = obras_projetadas / META_OBRAS_ANUAL if META_OBRAS_ANUAL else 0

    dados["kpis"] = {
        "recorrente_ativo": recorrente_ativo,
        "recorrente_ativo_fmt": _brl(recorrente_ativo),
        "n_contratos": len(contratos),
        "meta_recorrente": META_RECORRENTE_MENSAL,
        "pct_recorrente": round(pct_rec * 100, 1),
        "gap_recorrente": max(0.0, META_RECORRENTE_MENSAL - recorrente_ativo),
        "obras_faturadas": obras_faturadas,
        "obras_faturadas_fmt": _brl(obras_faturadas),
        "obras_ganhas": obras_ganhas,
        "n_obras_ganhas": len(obras_ganhas),
        "meta_obras": META_OBRAS_ANUAL,
        "pct_obras": round(pct_obras * 100, 1),
        "pipeline_ativo": pipeline_ativo,
        "recorrente_ponderado": round(recorrente_ponderado, 0),
        "recorrente_ponderado_fmt": _brl(recorrente_ponderado),
        # --- concentração de cliente (sug. 1) ---
        "concentracao_cliente": maior_grupo,
        "concentracao_valor": round(maior_grupo_valor, 2),
        "concentracao_valor_fmt": _brl(maior_grupo_valor),
        "concentracao_pct": round(pct_concentracao, 1),
        # --- meta de obras projetada (sug. 2) ---
        "obras_ponderadas": round(obras_ponderadas, 0),
        "obras_ponderadas_fmt": _brl(obras_ponderadas),
        "obras_projetadas": round(obras_projetadas, 0),
        "obras_projetadas_fmt": _brl(obras_projetadas),
        "pct_obras_proj": round(pct_obras_proj * 100, 1),
        # --- qualidade do dado do pipeline (sug. 3) ---
        "pipe_sem_prob": pipe_sem_prob,
        "pipe_sem_rec": pipe_sem_rec,
        "pipe_valor_sem_prob": round(pipe_valor_sem_prob, 0),
        "pipe_valor_sem_prob_fmt": _brl(pipe_valor_sem_prob) if pipe_valor_sem_prob else "—",
    }
    dados["pipeline"] = pipeline
    dados["licitacoes"] = licitacoes
    dados["lic_kpis"] = {
        "total": len(licitacoes),
        # contagens (aba LICITAÇÕES)
        "em_pregao": lic_cont["emprego"],
        "em_elaboracao": lic_cont["elaboracao"],
        "proposta_enviada": lic_cont["propostaenv"],
        "habilitado": lic_cont["habilitado"],
        "nao_iniciado": lic_cont["naoiniciado"],
        "nao_participada": lic_cont["naopart"],
        # valores
        "valor_andamento": round(lic_valor["andamento"], 2),
        "valor_andamento_fmt": _brl(lic_valor["andamento"]) if lic_valor["andamento"] else "—",
        "valor_ganho": round(lic_ganho, 2),
        "valor_ganho_fmt": _brl(lic_ganho) if lic_ganho else "—",
        "valor_perdido": round(lic_perdido, 2),
        "valor_perdido_fmt": _brl(lic_perdido) if lic_perdido else "—",
        "valor_naopart": round(lic_valor["naopart"], 2),
        "valor_naopart_fmt": _brl(lic_valor["naopart"]) if lic_valor["naopart"] else "—",
        # quantidades por trás de cada valor
        "qtd_andamento": lic_and_qtd,
        "qtd_ganho": lic_qtd_ganho,
        "qtd_perdido": lic_qtd_perdido,
        # cobertura: quantas linhas em andamento estão sem VALOR ESTIMADO.
        # Enquanto > 0, o valor_andamento está incompleto e o dashboard avisa.
        "qtd_sem_valor": lic_and_sem_valor,
        "cobertura_ok": lic_and_sem_valor == 0,
    }
    dados["funil"] = [
        {"l": "🔵 Prospecção", "q": funil["prospeccao"]},
        {"l": "📤 Proposta enviada", "q": funil["enviada"]},
        {"l": "🔄 Em andamento", "q": funil["andamento"]},
        {"l": "🟣 Em negociação", "q": funil["negociacao"]},
        {"l": "✅ Fechado / ganho", "q": funil["ganho"]},
        {"l": "❌ Perdido", "q": funil["perdido"]},
    ]
    dados["contratos"] = contratos
    dados["prospec"] = prospec
    dados["historico"] = historico
    # análise de conversão (sug. 5) — 'amostra' sinaliza base pequena
    dados["analise"] = {
        "amostra": amostra_hist,
        "conversao_origem": conversao_origem,
        "motivos_perda": motivos_perda_lista,
    }
    dados["_meta"] = {
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    return dados


# ------------------------------------------------------------------ MAIN
def main():
    try:
        planilha = achar_planilha(PASTA_COMERCIAL)
    except FileNotFoundError as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        return 2

    print(f"[info] planilha: {planilha}")
    try:
        wb = openpyxl.load_workbook(planilha, data_only=True)
    except Exception as e:
        print(f"[ERRO] não consegui abrir a planilha: {e}", file=sys.stderr)
        return 3

    try:
        dados = extrair(wb)
    except Exception as e:
        print(f"[ERRO] falha ao extrair dados: {e}", file=sys.stderr)
        return 4

    saida = Path(SAIDA_JSON)
    try:
        saida.parent.mkdir(parents=True, exist_ok=True)
        with open(saida, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except PermissionError:
        print(
            f"[ERRO] sem permissão para gravar em {saida}.\n"
            "       Ajuste o dono/ACL da pasta data/ no TrueNAS "
            "(o container precisa escrever nela).",
            file=sys.stderr,
        )
        return 5
    except Exception as e:
        print(f"[ERRO] falha ao gravar o JSON: {e}", file=sys.stderr)
        return 6

    k = dados["kpis"]
    print(f"[ok] {saida}")
    print(f"     recorrente ativo : {k['recorrente_ativo_fmt']} ({k['pct_recorrente']}%)")
    print(f"       concentração   : {k['concentracao_pct']}% em {k['concentracao_cliente']} "
          f"({k['concentracao_valor_fmt']})")
    print(f"     obras faturadas  : {k['obras_faturadas_fmt']} ({k['pct_obras']}%)")
    print(f"       + projetado    : {k['obras_projetadas_fmt']} ({k['pct_obras_proj']}%) "
          f"[inclui pipeline ponderado {k['obras_ponderadas_fmt']}]")
    print(f"     pipeline exibido : {k['pipeline_ativo']} itens")
    if k["pipe_sem_prob"]:
        print(f"       [dado] {k['pipe_sem_prob']} linha(s) sem probabilidade "
              f"({k['pipe_valor_sem_prob_fmt']} em obras fora do ponderado)")
    lk = dados["lic_kpis"]
    print(f"     licitações       : {lk['total']}")
    print(f"       em andamento   : {lk['valor_andamento_fmt']} ({lk['qtd_andamento']} proc.)")
    print(f"       ganho          : {lk['valor_ganho_fmt']} ({lk['qtd_ganho']} proc.)")
    print(f"       perdido        : {lk['valor_perdido_fmt']} ({lk['qtd_perdido']} proc.)")
    if not lk["cobertura_ok"]:
        print(f"     [ATENÇÃO] {lk['qtd_sem_valor']} de {lk['qtd_andamento']} licitações em "
              f"andamento sem VALOR ESTIMADO — o total em andamento está incompleto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
