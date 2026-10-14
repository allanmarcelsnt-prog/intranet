"""
seed.py — popula os JSONs iniciais da Licitação.
Rode uma vez (dentro do container ou no dev):  python seed.py
"""
import json
import tempfile
import os
from pathlib import Path

import auth_store

BASE = Path(__file__).parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

# equipe no AUTH CENTRAL (sem senha — cada um cria no 1º acesso)
equipe = [
    {"nome": "Bruno Teixeira",     "usuario": "bruno",   "papel": "coordenador"},
    {"nome": "Thiago Dames",       "usuario": "thiago",  "papel": "analista"},
    {"nome": "Luana Mota",         "usuario": "luana",   "papel": "analista"},
    {"nome": "Adriano Sávio",      "usuario": "adriano", "papel": "comercial"},
    {"nome": "Osvair",             "usuario": "osvair",  "papel": "operacional"},
    {"nome": "Rodrigo Iannibelli", "usuario": "rodrigo", "papel": "atribuidor"},
    {"nome": "Rafael",             "usuario": "rafael",  "papel": "operacional"},
    {"nome": "Pedro Mendes",       "usuario": "pedro",   "papel": "operacional"},
    {"nome": "Allan Marcel",       "usuario": "allan",   "papel": "coordenador"},
]
auth_store.semear_equipe(equipe)
print(f"✓ {len(equipe)} usuários no auth central (definem senha no 1º acesso)")


def _grava(nome, obj):
    path = DATA / nome
    fd, tmp = tempfile.mkstemp(dir=str(DATA), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


AGORA = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")

pregoes_path = DATA / "licitacoes_pregoes.json"
pregoes = json.load(open(pregoes_path, encoding="utf-8")) if pregoes_path.exists() else []
if not any(p["numero"] == "24/2026" for p in pregoes):
    itens_base = [
        ["1", "Gravador de Vídeo Digital (NVD) 16 canais IP, PoE, H.265, ONVIF 2.4+", 9, 2536.18],
        ["2", "Câmera Bullet IP Full HD, PoE, IP67, compatível com NVD", 28, 732.46],
        ["3", "Câmera Dome IP Full HD, PoE, IP67, compatível com NVD", 57, 679.70],
        ["4", "Nobreak 2 kVA senoidal, bivolt, 7 tomadas", 13, 3253.38],
        ["5", "HD 8 TB para CFTV, SATA III, 24x7", 5, 2105.21],
    ]
    pregoes.append({
        "id": 1, "orgao": "MPF — Procuradoria da República no RJ", "numero": "24/2026",
        "uasg": "200043", "portal": "Compras.gov.br",
        "objeto": "Equipamentos de CFTV — câmeras IP, NVD, nobreaks e HDs Purple",
        "tipo": "fornecimento", "tem_referencia": True, "criterio": "Menor preço por item",
        "modo_disputa": "Aberto e fechado", "exclusivo_me_epp": "sim",
        "valor_estimado": 134897.39, "data_sessao": "2026-08-18T10:00",
        "status": "em_cotacao", "responsavel": None, "pasta_truenas": None,
        "criado_por": "Allan Marcel", "criado_em": AGORA, "atualizado_em": AGORA,
        "itens": [
            {"id": i + 1, "numero_item": n, "descricao": d, "quantidade": q,
             "teto_unitario": t, "fornecemos": None, "validado": "pendente",
             "motivo_reprova": None, "incluido_adriano": False, "sugestoes": [],
             "sugestao_escolhida_id": None, "datasheet_path": None}
            for i, (n, d, q, t) in enumerate(itens_base)
        ],
    })
    _grava("licitacoes_pregoes.json", pregoes)
    print("✓ Pregão de exemplo MPF 24/2026 com 5 itens")

for nome, vazio in [("licitacoes_auditoria.json", {"etapas": [], "log": []}),
                    ("licitacoes_licoes.json", [])]:
    if not (DATA / nome).exists():
        _grava(nome, vazio)

print("Seed concluído.")
