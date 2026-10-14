"""
Extrai itens de um edital/termo de referência em PDF.

Estratégia:
  1. Procura, em todas as páginas, uma tabela cujo cabeçalho tenha colunas de
     ESPECIFICAÇÃO/DESCRIÇÃO + QTDE + VALOR UNITÁRIO (a "tabela-resumo" de itens).
  2. Recupera o número do item (coluna à esquerda, muitas vezes mesclada) casando
     as linhas da tabela por posição vertical com números isolados na margem.
  3. Normaliza quantidade (int) e valor unitário (float, formato BR).

Uso: extrair_itens_pdf(caminho_pdf) -> {"itens":[...], "aviso":..., "origem":...}
"""
import re, unicodedata

def _norm(s):
    if not s: return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii","ignore").decode("ascii")
    return s.lower().strip()

def _num_br(s):
    """'2.536,18' -> 2536.18 ; '09' -> 9 ; '' -> None"""
    if s is None: return None
    s = str(s).strip()
    if not s: return None
    s = re.sub(r"[^\d.,]", "", s)
    if not s: return None
    # formato BR: ponto=milhar, vírgula=decimal
    s = s.replace(".", "").replace(",", ".")
    try:
        f = float(s)
        return f
    except ValueError:
        return None

def _achar_colunas(header):
    """Mapeia índices das colunas de interesse a partir do cabeçalho."""
    idx = {"desc":None, "qtd":None, "valor":None, "item":None, "unid":None}
    for i, c in enumerate(header):
        n = _norm(c)
        if idx["item"] is None and n in ("item","itens","ite m","ite\nm"):
            idx["item"] = i
        elif idx["desc"] is None and ("especifica" in n or "descri" in n):
            idx["desc"] = i
        elif idx["qtd"] is None and ("qtde" in n or "quant" in n or n=="qtd"):
            idx["qtd"] = i
        elif idx["unid"] is None and "unidade" in n:
            idx["unid"] = i
        elif idx["valor"] is None and "valor" in n and ("unit" in n):
            idx["valor"] = i
    return idx

def _eh_tabela_itens(header):
    idx = _achar_colunas(header)
    return idx["desc"] is not None and idx["qtd"] is not None

def extrair_itens_pdf(caminho):
    import pdfplumber
    itens = []
    aviso = None
    origem = None
    with pdfplumber.open(caminho) as pdf:
        for pnum, page in enumerate(pdf.pages, start=1):
            for tab in page.extract_tables():
                if not tab or len(tab) < 2:
                    continue
                header = tab[0]
                if not _eh_tabela_itens(header):
                    continue
                idx = _achar_colunas(header)
                # recupera números de item pela margem esquerda (coluna mesclada)
                nums_por_top = []
                if idx["item"] is None:
                    for w in page.extract_words():
                        t = w["text"].strip()
                        if re.fullmatch(r"0?\d{1,2}", t) and w["x0"] < 130:
                            nums_por_top.append((w["top"], t))
                    nums_por_top.sort()
                linhas = tab[1:]
                for li, row in enumerate(linhas):
                    desc = (row[idx["desc"]] or "").strip() if idx["desc"] is not None else ""
                    desc = re.sub(r"\s+", " ", desc).rstrip(":").strip()
                    if not desc:
                        continue
                    qtd = _num_br(row[idx["qtd"]]) if idx["qtd"] is not None else None
                    valor = _num_br(row[idx["valor"]]) if idx["valor"] is not None else None
                    # número do item
                    if idx["item"] is not None:
                        numero = (row[idx["item"]] or "").strip()
                    elif li < len(nums_por_top):
                        numero = nums_por_top[li][1]
                    else:
                        numero = str(li+1)
                    numero = numero.lstrip("0") or str(li+1)
                    itens.append({
                        "numero_item": numero,
                        "descricao": desc,
                        "quantidade": int(qtd) if qtd is not None else None,
                        "teto_unitario": valor,
                    })
                if itens:
                    origem = f"tabela na página {pnum} de {caminho.split('/')[-1]}"
                    break
            if itens:
                break
    if not itens:
        aviso = "Não encontrei uma tabela de itens reconhecível. Cadastre manualmente."
    elif any(i["teto_unitario"] is None for i in itens):
        aviso = "Alguns itens vieram sem valor unitário — confira antes de salvar."
    return {"itens": itens, "aviso": aviso, "origem": origem}


if __name__ == "__main__":
    import json, sys
    r = extrair_itens_pdf(sys.argv[1])
    print("origem:", r["origem"])
    print("aviso :", r["aviso"])
    for it in r["itens"]:
        print(f"  [{it['numero_item']}] {it['descricao'][:44]:44} qtd={it['quantidade']} teto={it['teto_unitario']}")
    print("total:", len(r["itens"]), "itens")
