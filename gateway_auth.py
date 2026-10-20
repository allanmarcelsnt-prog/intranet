"""
gateway_auth.py — login único (Keycloak) na frente da intranet.

DESLIGADO por padrão: só vale quando KEYCLOAK_ISSUER está definido no ambiente.
Com ele desligado, nada muda no comportamento atual (cookie intranet_sessao).

Como funciona quando ligado
  navegador -> oauth2-proxy (faz o login no Keycloak) -> este backend
  O oauth2-proxy repassa o access token do Keycloak no cabeçalho
  X-Forwarded-Access-Token. Aqui o token é VALIDADO de verdade (assinatura pelo
  JWKS do Keycloak, emissor, validade e cliente), então um acesso direto à porta
  do backend com cabeçalho forjado não passa.

  1. Páginas/rotas públicas (candidatos, autoavaliação, health) seguem abertas.
  2. Todo o resto exige token válido (senão 401 -> o proxy manda para o login).
  3. Rotas de apps com permissão própria (Frota, Monitoramento) exigem o grupo
     GS_App_<App> no token (senão 403).
  4. usuario_logado (auth_routes) passa a aceitar a identidade do Keycloak,
     ligando o e-mail ao usuário local — sem segundo login dentro do RH etc.

Variáveis de ambiente
  KEYCLOAK_ISSUER   ex.: http://192.168.0.171:8190/realms/safenet   (liga o modo)
  KEYCLOAK_CLIENT   client do portal (padrão: intranet)
  GATEWAY_REGRAS    JSON {"/prefixo/": "GS_App_X"} (padrão: Frota e Monitoramento)
"""
import json
import os
import re
from typing import Optional

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse

ISSUER = os.environ.get("KEYCLOAK_ISSUER", "").rstrip("/")
CLIENTE = os.environ.get("KEYCLOAK_CLIENT", "intranet")
ATIVO = bool(ISSUER)

# Prefixo -> grupo exigido. Quem não tem o grupo recebe 403 (além do portal).
REGRAS = {
    "/frota": "GS_App_Frota", "/api/frota": "GS_App_Frota",
    "/monitoramento": "GS_App_Monitoramento", "/api/monitoramento": "GS_App_Monitoramento",
}
if os.environ.get("GATEWAY_REGRAS"):
    REGRAS = json.loads(os.environ["GATEWAY_REGRAS"])

# Abertos mesmo com o modo ligado. TEM que bater com --skip-auth-regex do proxy.
PUBLICO = re.compile(
    r"^/(health$|favicon|logo\.png$|candidatura/|api/candidatura/|rh/vaga/"
    r"|api/rh/desempenho/public/|rh/autoavaliacao\.html$|rh/avaliacao_avaliador\.html$"
    r"|rh/avaliacao_supervisor\.html$)"
)

_jwks = None


def _cliente_jwks():
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"{ISSUER}/protocol/openid-connect/certs", cache_keys=True)
    return _jwks


def _chave(token: str):
    return _cliente_jwks().get_signing_key_from_jwt(token).key


def validar_token(token: str) -> Optional[dict]:
    """Valida o access token do Keycloak. Devolve as claims ou None."""
    try:
        claims = jwt.decode(token, _chave(token), algorithms=["RS256"], issuer=ISSUER,
                            options={"verify_aud": False, "require": ["exp", "iss"]})
    except Exception:
        return None
    if claims.get("azp") != CLIENTE:
        return None
    return claims


def identidade(request: Request) -> Optional[dict]:
    """Identidade do Keycloak desta requisição (ou None). Guarda em request.state."""
    if not ATIVO:
        return None
    if hasattr(request.state, "kc"):
        return request.state.kc
    token = request.headers.get("x-forwarded-access-token", "")
    if not token and request.headers.get("authorization", "").lower().startswith("bearer "):
        token = request.headers["authorization"][7:]
    claims = validar_token(token) if token else None
    ident = None
    if claims:
        ident = {"email": (claims.get("email") or "").lower(),
                 "usuario": claims.get("preferred_username", ""),
                 "nome": claims.get("name", ""),
                 "grupos": list(claims.get("groups") or [])}
    request.state.kc = ident
    return ident


def grupo_exigido(path: str) -> Optional[str]:
    for prefixo, grupo in REGRAS.items():
        if path == prefixo or path.startswith(prefixo + "/"):
            return grupo
    return None


async def middleware(request: Request, call_next):
    path = request.url.path
    if not ATIVO or PUBLICO.match(path):
        return await call_next(request)
    ident = identidade(request)
    if ident is None:
        return JSONResponse({"detail": "Faça login com a conta Safenet."}, status_code=401)
    grupo = grupo_exigido(path)
    if grupo and grupo not in ident["grupos"]:
        return JSONResponse({"detail": "Você não tem acesso a este app."}, status_code=403)
    return await call_next(request)
