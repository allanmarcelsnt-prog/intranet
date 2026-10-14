#!/usr/bin/env python3
"""
seed_colaboradores.py — MAP-RH-002
===================================
Popula desempenho_colaboradores.json com os 37 colaboradores do escopo inicial
(Matriz RJ), lendo intranet_usuarios.json para resolver os avaliador_id.

Como executar (escolha uma das duas formas):

  A) Dentro do container:
     docker exec intranet-safenet python3 /app/seed_colaboradores.py

  B) No host TrueNAS com acesso ao dataset:
     cd /mnt/Safenet_Storage/intranet
     python3 seed_colaboradores.py

O script:
  - Lê os logins existentes em intranet_usuarios.json
  - Cria desempenho_colaboradores.json com os 37 colaboradores
  - Define avaliador_id pelo login de cada avaliador
  - Deixa data_admissao em branco (preencher via interface)
  - Deixa supervisor_id null (preencher após criar logins dos supervisores)
  - Não sobrescreve sem confirmação se já houver dados
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
USUARIOS_PATH = BASE / "data" / "intranet_usuarios.json"
COLAB_PATH    = BASE / "data" / "desempenho_colaboradores.json"

AGORA = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
        raise e


def carregar_uid_map() -> dict:
    """Retorna {login: id} de todos os usuários ativos."""
    if not USUARIOS_PATH.exists():
        print(f"  ⚠  {USUARIOS_PATH} não encontrado — avaliador_id ficará 0 para todos.")
        return {}
    with USUARIOS_PATH.open("r", encoding="utf-8") as f:
        usuarios = json.load(f)
    mapa = {u["usuario"]: u["id"] for u in usuarios if u.get("ativo", True)}
    print(f"  Usuários ativos encontrados: {list(mapa.keys())}")
    return mapa


# ---------------------------------------------------------------------------
# Lista de colaboradores — extraída da planilha da Rafaela (MAP-RH-002 §13)
# Formato: (nome, cargo, setor, email, vinculo, login_avaliador, tem_supervisor)
# tem_supervisor=True → Operacional técnico com co-avaliação de supervisor de campo
# ---------------------------------------------------------------------------
COLABORADORES_RAW = [
    # ── OPERACIONAL ──────────────────────────────────────────────────────────
    # Supervisores Técnicos — avaliados só pelo Rodrigo
    ("Adriano dos Santos Moura",       "Supervisor Técnico",          "Operacional",    "adriano.moura@safenettecnologia.com.br",       "CLT",       "riannibelli", False),
    ("Leonardo César F. M. Santos",    "Supervisor Técnico",          "Operacional",    "leonardo.santos@safenettecnologia.com.br",     "CLT",       "riannibelli", False),
    ("Milton Ferreira dos Santos",     "Supervisor Técnico",          "Operacional",    "milton.santos@safenettecnologia.com.br",       "CLT",       "riannibelli", False),
    # Técnicos, Auxiliares, Instaladores — Rodrigo + Supervisor de campo
    ("Breno Cordeiro de Andrade",      "Auxiliar Técnico I",          "Operacional",    "breno.andrade@safenettecnologia.com.br",      "CLT",       "riannibelli", True),
    ("David de Lima Bezerra",          "Auxiliar Técnico I",          "Operacional",    "david.bezerra@safenettecnologia.com.br",      "CLT",       "riannibelli", True),
    ("Diego Oliveira Costa",           "Técnico de Instalação",       "Operacional",    "diego.costa@safenettecnologia.com.br",        "CLT",       "riannibelli", True),
    ("Fernando Romão C. K. Pereira",   "Instalador Eletroeletrônico", "Operacional",    "fernando.pereira@safenettecnologia.com.br",   "CLT",       "riannibelli", True),
    ("Fernando Sabino Falleiro Alves", "Técnico em Eletrônica",       "Operacional",    "fernando.alves@safenettecnologia.com.br",     "CLT",       "riannibelli", True),
    ("Harllen Francisco da Silva",     "Auxiliar Técnico I",          "Operacional",    "harllen.silva@safenettecnologia.com.br",      "CLT",       "riannibelli", True),
    ("Lauro Augusto S. H. Barbosa",    "Auxiliar Técnico I",          "Operacional",    "lauro.barbosa@safenettecnologia.com.br",      "CLT",       "riannibelli", True),
    ("Matthieu Stewart Martinez",      "Auxiliar Técnico I",          "Operacional",    "matthieu.martinez@safenettecnologia.com.br",  "CLT",       "riannibelli", True),
    ("Sandro Alves da Cruz",           "Instalador Eletroeletrônico", "Operacional",    "sandro.cruz@safenettecnologia.com.br",        "CLT",       "riannibelli", True),
    ("Thyago Luiz Barros Almeida",     "Técnico em Seg. do Trabalho", "Operacional",    "thyago.almeida@safenettecnologia.com.br",     "CLT",       "riannibelli", False),
    ("Yan Anthony Batista da Costa",   "Técnico em Eletrônica",       "Operacional",    "yan.costa@safenettecnologia.com.br",          "CLT",       "riannibelli", True),
    # Outros Operacional
    ("Leonardo de Souza Medeiros",     "Assistente Operacional",      "Operacional",    "leonardo.medeiros@safenettecnologia.com.br",  "CLT",       "riannibelli", False),
    ("Osvair Torraca",                 "Projetista",                  "Operacional",    "osvair.torraca@safenettecnologia.com.br",     "PJ",        "riannibelli", False),
    ("Brenno R. G. Dib Ferreira",      "Estagiário Engenharia",       "Operacional",    "brenno.ferreira@safenettecnologia.com.br",    "Estagiário","riannibelli", True),
    # Coordenador Operacional — avaliado pelos diretores
    ("Rodrigo Iannibelli",             "Coordenador Operacional",     "Operacional",    "rodrigo.iannibelli@safenettecnologia.com.br", "CLT",       "asavio",      False),
    # ── MONITORAMENTO ────────────────────────────────────────────────────────
    ("Amanda Correa Fernandes",        "Operador de Monitoramento",   "Monitoramento",  "amanda.fernandes@safenettecnologia.com.br",   "CLT",       "abarros",     False),
    ("Daniel Silva Martins Carvalho",  "Operador de Monitoramento",   "Monitoramento",  "daniel.carvalho@safenettecnologia.com.br",    "CLT",       "abarros",     False),
    ("Lázaro Barcellos de Toledo",     "Operador de Monitoramento",   "Monitoramento",  "lazaro.toledo@safenettecnologia.com.br",      "CLT",       "abarros",     False),
    ("Lucas Tavares Hinds",            "Operador de Monitoramento",   "Monitoramento",  "lucas.hinds@safenettecnologia.com.br",        "CLT",       "abarros",     False),
    ("Luiz Marcelo Soares Calheiros",  "Operador de Monitoramento",   "Monitoramento",  "luiz.calheiros@safenettecnologia.com.br",     "CLT",       "abarros",     False),
    ("Marcelo Gomes da Silva",         "Operador de Monitoramento",   "Monitoramento",  "marcelo.silva@safenettecnologia.com.br",      "CLT",       "abarros",     False),
    # ── PROJETOS ─────────────────────────────────────────────────────────────
    ("Lucas F. M. Lopes da Silva",     "Estagiário Arquitetura",      "Projetos",       "lucas.lopes@safenettecnologia.com.br",        "Estagiário","abarros",     False),
    ("Jorge Lucas de Oliveira Silva",  "Estagiário Eletrotécnica",    "Projetos",       "jorge.silva@safenettecnologia.com.br",        "Estagiário","abarros",     False),
    ("Poliana Rocha de Almeida",       "Estagiária Arquitetura",      "Projetos",       "poliana.almeida@safenettecnologia.com.br",    "Estagiária","abarros",     False),
    # ── TI ───────────────────────────────────────────────────────────────────
    ("Carlos Henrique O. de Almeida",  "Estagiário TI e Suporte",     "TI",             "carlos.almeida@safenettecnologia.com.br",     "Estagiário","abarros",     False),
    # Coordenador TI — avaliado pelos diretores
    ("Allan Marcel de Lima M. Barros", "Coordenador Técnico",         "TI",             "allan.marcel@safenettecnologia.com.br",       "CLT",       "asavio",      False),
    # ── ADMINISTRATIVO ───────────────────────────────────────────────────────
    ("Elaine Marcia da Silva Martinho","Aux. Serviços Gerais I",       "Administrativo", "elaine.martinho@safenettecnologia.com.br",    "CLT",       "pmendes",     False),
    ("Rafael Roberto de Oliveira Pires","Auxiliar de Logística I",    "Administrativo", "rafael.pires@safenettecnologia.com.br",       "CLT",       "pmendes",     False),
    ("Rafaela da Silva Martinho",      "Auxiliar Administrativo I",   "Administrativo", "rafaela.martinho@safenettecnologia.com.br",   "CLT",       "pmendes",     False),
    ("Rafaela Fernandes Cerqueira",    "Auxiliar Administrativo I",   "Administrativo", "rafaela.cerqueira@safenettecnologia.com.br",  "CLT",       "pmendes",     False),
    # Coordenador Administrativo — avaliado pelos diretores
    ("Pedro Henrique N. Mendes",       "Coordenador Administrativo",  "Administrativo", "pedro.mendes@safenettecnologia.com.br",       "CLT",       "asavio",      False),
    # ── LICITAÇÃO ────────────────────────────────────────────────────────────
    # Coordenador de Licitação — avaliado pelos diretores
    ("Bruno do Nascimento Teixeira",   "Coordenador de Licitação",    "Licitação",      "bruno.teixeira@safenettecnologia.com.br",     "PJ",        "asavio",      False),
    ("Thiago de Oliveira D. Monteiro", "Analista de Processos",       "Licitação",      "thiago.monteiro@safenettecnologia.com.br",    "PJ",        "bteixeira",   False),
    ("Luana Morais Mota",              "Estagiária Engenharia",       "Licitação",      "luana.mota@safenettecnologia.com.br",         "Estagiária","bteixeira",   False),
]


def main():
    print("=" * 60)
    print("seed_colaboradores.py — MAP-RH-002")
    print("=" * 60)

    # Verificar se já existe dados
    if COLAB_PATH.exists():
        with COLAB_PATH.open("r", encoding="utf-8") as f:
            existentes = json.load(f)
        if existentes:
            print(f"\n⚠  {COLAB_PATH} já contém {len(existentes)} colaborador(es).")
            resp = input("   Substituir tudo e começar do zero? (s/N): ").strip().lower()
            if resp != "s":
                print("   Abortado. Nenhum dado foi alterado.")
                sys.exit(0)

    uid = carregar_uid_map()

    # Logins dos avaliadores esperados
    logins_esperados = {"riannibelli", "abarros", "pmendes", "bteixeira", "asavio"}
    nao_encontrados  = logins_esperados - set(uid.keys())
    if nao_encontrados:
        print(f"\n  ⚠  Logins não encontrados (avaliador_id ficará 0): {nao_encontrados}")
        print("     Crie os usuários antes de rodar o seed, ou corrija via interface depois.\n")

    colaboradores = []
    avisos_av0 = []

    for i, (nome, cargo, setor, email, vinculo, av_login, tem_sup) in enumerate(
            COLABORADORES_RAW, start=1):

        av_id = uid.get(av_login, 0)
        if av_id == 0:
            avisos_av0.append(f"  [{i:02d}] {nome} — avaliador login '{av_login}' não encontrado")

        colab = {
            "id":             i,
            "nome":           nome,
            "cargo":          cargo,
            "setor":          setor,
            "email":          email,
            "data_admissao":  "",          # ← preencher via interface após o seed
            "vinculo":        vinculo,
            "avaliador_id":   av_id,
            "supervisor_id":  None,        # ← preencher após criar logins dos supervisores
            "ativo":          True,
            "cadastrado_por": "sistema (seed inicial MAP-RH-002)",
            "cadastrado_em":  AGORA(),
            # Nota interna para o RH saber que tem co-avaliação pendente
            "_pendente_supervisor": tem_sup,
        }
        colaboradores.append(colab)

    _escrever_atomico(colaboradores, COLAB_PATH)

    print(f"\n✅  {len(colaboradores)} colaboradores gravados em:")
    print(f"    {COLAB_PATH}")

    # Resumo por setor
    print("\n  Distribuição por setor:")
    setores: dict = {}
    for c in colaboradores:
        s = c["setor"]
        setores[s] = setores.get(s, 0) + 1
    for s, n in sorted(setores.items()):
        print(f"    {s:<18} {n:>2} colaborador(es)")

    # Avisos
    com_supervisor = sum(1 for c in colaboradores if c.get("_pendente_supervisor"))
    print(f"\n  Operacional com co-avaliação de supervisor: {com_supervisor}")

    if avisos_av0:
        print("\n  ⚠  avaliador_id=0 (login não encontrado):")
        for a in avisos_av0:
            print(a)

    print("\n" + "=" * 60)
    print("PRÓXIMOS PASSOS (ações manuais necessárias):")
    print("=" * 60)
    print("""
  1. DATA DE ADMISSÃO (obrigatória para os gatilhos automáticos)
     → Rafaela acessa /rh/ → Avaliações → Colaboradores → Editar
     → Preenche data_admissao de cada colaborador pela planilha
     → Sem essa data os gatilhos de 35d/80d/semestral não disparam

  2. SUPERVISOR DE CAMPO (co-avaliação Operacional)
     → Allan cria logins com papel 'supervisor_campo' para:
         • Adriano dos Santos Moura
         • Leonardo César F. M. Santos
         • Milton Ferreira dos Santos
     → Após criar, Rafaela edita cada técnico/auxiliar do Operacional
       e preenche supervisor_id com o ID do supervisor responsável

  3. AVALIADOR_ID = 0 (se houver)
     → Criar os logins que faltam (asavio, bteixeira, etc.) e rodar
       novamente este script, ou corrigir individualmente via interface

  4. E-MAIL DA ELAINE
     → Confirmar se elaine.martinho@safenettecnologia.com.br já existe
     → Se não existir, criar antes que o primeiro ciclo seja disparado
""")


if __name__ == "__main__":
    main()
