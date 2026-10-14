#!/usr/bin/env python3
"""
gerar_json_noticias.py — Agregador de noticias para a Intranet Safenet
----------------------------------------------------------------------
Le feeds RSS de portais de tecnologia e seguranca eletronica e gera
data/noticias.json, consumido pelo bloco de noticias do index.html.

Padrao "Caminho A": Fonte -> Python -> JSON -> FastAPI -> HTML.

Agendamento: cron da WebUI do TrueNAS (System -> Advanced -> Cron Jobs).
Sugestao: 0 * * * *  (de hora em hora)

Somente biblioteca padrao — nao depende de feedparser/requests, entao
sobrevive a recriacao do container sem precisar reinstalar nada.

IMPORTANTE (direito autoral): guardamos apenas titulo, resumo curto e o
link de volta para o portal original. Nunca o texto integral da materia.
"""
import html
import json
import os
import re
import ssl
import tempfile
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIGURACAO — edite apenas esta secao
# ---------------------------------------------------------------------------
BASE = Path(__file__).parent
SAIDA = BASE / "data" / "noticias.json"

# Para adicionar uma fonte, basta acrescentar um dicionario aqui.
# "setor": True marca a fonte como do ramo (ganha destaque no bloco).
FONTES = [
    {
        "nome":  "CISO Advisor",
        "url":   "https://www.cisoadvisor.com.br/feed/",
        "setor": True,
    },
    {
        "nome":  "Tecnoblog",
        "url":   "https://tecnoblog.net/feed/",
        "setor": False,
    },
    {
        "nome":  "Canaltech",
        "url":   "https://canaltech.com.br/rss/",
        "setor": False,
    },
    {
        "nome":  "Olhar Digital",
        "url":   "https://olhardigital.com.br/feed/",
        "setor": False,
    },
    # Fontes descartadas nos testes:
    # - Guia do CFTV: servidor nao envia a cadeia intermediaria do
    #   certificado; a verificacao SSL falha dentro do container.
    # - Revista Seguranca Eletronica: o /feed/ devolve uma pagina HTML de
    #   protecao anti-bot em vez do RSS.
    # Reabilitar caso corrijam do lado deles.
]

MAX_POR_FONTE   = 12   # itens lidos de cada feed (antes do filtro)
MAX_TOTAL       = 12   # itens gravados no JSON
MIN_SETOR       = 6    # vagas reservadas a fontes do ramo (setor: True)
RESUMO_MAX      = 180  # caracteres do resumo (trecho curto, com link de volta)
TIMEOUT         = 20
USER_AGENT      = "Mozilla/5.0 (compatible; SafenetIntranet/1.0)"

# Portais de tecnologia publicam muito conteudo comercial e de afiliado
# ("X em promocao", "melhores Y baratos"). Nao serve para intranet
# corporativa — descartamos pelo titulo.
PALAVRAS_BLOQUEADAS = [
    "promoção", "promocao", "oferta", "ofertas", "desconto", "cupom",
    "black friday", "cyber monday", "em conta", "bom e barato",
    "mais barato", "vale a pena comprar", "por menos de",
    "aproveite", "imperdível", "imperdivel", "compre",
]


NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media":   "http://search.yahoo.com/mrss/",
    "dc":      "http://purl.org/dc/elements/1.1/",
}


# ---------------------------------------------------------------------------
# UTILITARIOS
# ---------------------------------------------------------------------------
def limpar_html(texto: str) -> str:
    """Remove tags e comentarios, decodifica entidades, devolve texto puro."""
    if not texto:
        return ""
    texto = re.sub(r"<!--.*?-->", " ", texto, flags=re.DOTALL)
    texto = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", texto,
                   flags=re.DOTALL | re.IGNORECASE)
    texto = re.sub(r"<[^>]+>", " ", texto)
    # html.unescape cobre todas as entidades nomeadas e numericas
    # (&ecirc; &atilde; &#8230; etc) — melhor que uma lista manual.
    texto = html.unescape(texto)
    texto = html.unescape(texto)  # feeds costumam ter dupla codificacao
    return re.sub(r"\s+", " ", texto).strip()


def encurtar(texto: str, limite: int = RESUMO_MAX) -> str:
    """Corta no fim da ultima palavra inteira antes do limite."""
    texto = limpar_html(texto)
    if len(texto) <= limite:
        return texto
    corte = texto[:limite].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return corte + "..."


def parse_data(item) -> str:
    """Extrai a data de publicacao em ISO. Vazio se nao houver."""
    for tag in ("pubDate", "{http://purl.org/dc/elements/1.1/}date"):
        el = item.find(tag)
        if el is None or not (el.text or "").strip():
            continue
        bruto = el.text.strip()
        try:
            return parsedate_to_datetime(bruto).astimezone(
                timezone.utc).isoformat()
        except Exception:
            pass
        try:
            return datetime.fromisoformat(
                bruto.replace("Z", "+00:00")).astimezone(
                timezone.utc).isoformat()
        except Exception:
            pass
    return ""


def extrair_imagem(item) -> str:
    """Procura a imagem de destaque nos locais usuais do RSS."""
    for busca in ("media:content", "media:thumbnail"):
        el = item.find(busca, NS)
        if el is not None and el.get("url"):
            return el.get("url")
    enc = item.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image"):
        return enc.get("url") or ""
    for tag in ("{http://purl.org/rss/1.0/modules/content/}encoded",
                "description"):
        el = item.find(tag)
        if el is not None and el.text:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', el.text)
            if m:
                return m.group(1)
    return ""


def eh_comercial(titulo: str) -> bool:
    """True se o titulo parecer conteudo de promocao/afiliado."""
    t = titulo.lower()
    return any(p in t for p in PALAVRAS_BLOQUEADAS)


def texto_de(item, tag: str) -> str:
    el = item.find(tag)
    return (el.text or "").strip() if el is not None else ""


# ---------------------------------------------------------------------------
# LEITURA DOS FEEDS
# ---------------------------------------------------------------------------
def ler_feed(fonte: dict) -> list:
    """Le um feed. Nunca levanta excecao — devolve lista vazia em caso de erro."""
    try:
        req = urllib.request.Request(
            fonte["url"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            bruto = resp.read()
    except Exception as e:
        print(f"  [ERRO] {fonte['nome']}: {type(e).__name__}: {e}")
        return []

    try:
        raiz = ET.fromstring(bruto)
    except ET.ParseError as e:
        print(f"  [ERRO] {fonte['nome']}: XML invalido ({e})")
        return []

    itens = raiz.findall(".//item")                       # RSS 2.0
    if not itens:
        itens = raiz.findall(".//{http://www.w3.org/2005/Atom}entry")  # Atom
    if not itens:
        print(f"  [AVISO] {fonte['nome']}: nenhum item encontrado")
        return []

    noticias = []
    descartadas = 0
    for item in itens[:MAX_POR_FONTE]:
        titulo = limpar_html(texto_de(item, "title"))
        link   = texto_de(item, "link")
        if not link:  # Atom guarda o link num atributo
            el = item.find("{http://www.w3.org/2005/Atom}link")
            if el is not None:
                link = el.get("href", "")
        if not titulo or not link:
            continue
        if eh_comercial(titulo):
            descartadas += 1
            continue
        resumo = encurtar(texto_de(item, "description")
                          or texto_de(item, "{http://www.w3.org/2005/Atom}summary"))
        noticias.append({
            "titulo":    titulo,
            "resumo":    resumo,
            "link":      link,
            "fonte":     fonte["nome"],
            "setor":     fonte.get("setor", False),
            "imagem":    extrair_imagem(item),
            "publicado": parse_data(item),
        })

    extra = f" ({descartadas} comercial/is descartada/s)" if descartadas else ""
    print(f"  [OK]   {fonte['nome']}: {len(noticias)} noticia(s){extra}")
    return noticias


def escrever_atomico(obj, destino: Path):
    """Escrita atomica — mesmo padrao dos demais scripts da intranet."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(destino.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, destino)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Coletando noticias...")

    todas = []
    for fonte in FONTES:
        todas.extend(ler_feed(fonte))

    # remove duplicatas por link
    vistos, unicas = set(), []
    for n in todas:
        if n["link"] not in vistos:
            vistos.add(n["link"])
            unicas.append(n)

    # ordena por data (mais recentes primeiro); sem data vai para o fim
    unicas.sort(key=lambda n: n["publicado"] or "", reverse=True)

    # Selecao com cota: portais de tecnologia geral publicam muito mais que
    # os do ramo, e uma ordenacao simples por data expulsaria as noticias do
    # setor. Reservamos MIN_SETOR vagas para elas antes de completar o resto.
    do_setor = [n for n in unicas if n["setor"]]
    gerais   = [n for n in unicas if not n["setor"]]

    selecionadas = do_setor[:MIN_SETOR]
    restante = MAX_TOTAL - len(selecionadas)
    selecionadas += gerais[:restante]

    # se faltou material geral, completa com o excedente do setor
    if len(selecionadas) < MAX_TOTAL:
        ja = {n["link"] for n in selecionadas}
        selecionadas += [n for n in do_setor
                         if n["link"] not in ja][:MAX_TOTAL - len(selecionadas)]

    selecionadas.sort(key=lambda n: n["publicado"] or "", reverse=True)

    saida = {
        "noticias":     selecionadas,
        "total":        len(selecionadas),
        "fontes_ok":    sorted({n["fonte"] for n in selecionadas}),
        "atualizado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Se nenhuma fonte respondeu, preserva o JSON anterior em vez de
    # sobrescrever com uma lista vazia — o portal continua mostrando as
    # ultimas noticias conhecidas ate a proxima coleta dar certo.
    if not selecionadas and SAIDA.exists():
        print("  [AVISO] nenhuma fonte respondeu — JSON anterior preservado")
        return

    escrever_atomico(saida, SAIDA)
    print(f"  -> {SAIDA} ({len(selecionadas)} noticias, "
          f"fontes: {', '.join(saida['fontes_ok']) or 'nenhuma'})")


if __name__ == "__main__":
    main()
