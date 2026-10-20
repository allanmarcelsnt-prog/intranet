"""
Intranet Safenet — back-end
---------------------------
Serve a página-hub (static/) e expõe a base para os dados vivos (Caminho A).
Simples de propósito: um arquivo, fácil de manter.
"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from frota_routes import router as frota_router
from monitoramento_routes import router as monitoramento_router
from auth_routes import router as auth_router                 # autenticação central
from licitacoes_routes import router as licitacoes_router     # app de licitações
from rh_routes import router as rh_router, public_router as rh_public_router  # gestor de RH
from desempenho_routes import router as desempenho_router, public_router as desempenho_public_router  # avaliação de desempenho

BASE = Path(__file__).parent
STATIC = BASE / "static"
DATA = BASE / "data"          # onde o script do Caminho A vai gravar os JSON
DATA.mkdir(exist_ok=True)

app = FastAPI(title="Intranet Safenet", docs_url="/api/docs")

# Login único Keycloak (desligado a menos que KEYCLOAK_ISSUER esteja definido).
import gateway_auth
if gateway_auth.ATIVO:
    app.middleware("http")(gateway_auth.middleware)

# Rotas dos apps. TÊM que vir aqui, ANTES do mount("/") lá embaixo —
# senão o StaticFiles captura /api/... antes de chegar aqui.
app.include_router(frota_router)
app.include_router(monitoramento_router)
app.include_router(auth_router)
app.include_router(licitacoes_router)
app.include_router(rh_public_router)                          # formulário público (sem auth)
app.include_router(rh_router)                                 # gestor de RH (com auth)
app.include_router(desempenho_public_router)                    # autoavaliação pública (sem auth)
app.include_router(desempenho_router)                           # avaliação de desempenho (com auth)

@app.get("/health")
def health():
    """Usado para checar se o serviço está no ar."""
    return {"status": "ok", "servico": "intranet-safenet"}

@app.get("/api/dados/{nome}")
def dados(nome: str):
    """
    Entrega um JSON gerado pelo script do Caminho A.
    Ex.: /api/dados/comercial  -> data/comercial.json
    Enquanto o script não roda, responde de forma clara em vez de quebrar.
    """
    arquivo = DATA / f"{nome}.json"
    if not arquivo.exists():
        return JSONResponse(
            status_code=404,
            content={"erro": f"Sem dados para '{nome}' ainda.",
                     "dica": "O script do Caminho A ainda não gerou este arquivo."},
        )
    # O arquivo já é JSON válido em UTF-8; devolvemos como está, sem
    # re-serializar (evita que o conteúdo vire uma string escapada).
    return Response(
        content=arquivo.read_bytes(),
        media_type="application/json",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )

# A página-hub e o resto do site (tem que ser a ÚLTIMA rota registrada,
# senão ela captura tudo antes das rotas /api acima).
app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="site")
