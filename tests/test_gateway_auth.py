"""Testes do login único (gateway_auth). Rodar: KEYCLOAK_ISSUER=... pytest tests/"""
import importlib
import os
import sys
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

ISS = "http://kc.test/realms/safenet"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER", ISS)
    monkeypatch.setenv("INTRANET_DATA_DIR", str(tmp_path))
    for m in ("gateway_auth", "auth_routes", "auth_store", "main"):
        sys.modules.pop(m, None)
    import auth_store
    monkeypatch.setattr(auth_store, "_ler", lambda: [
        {"id": 1, "nome": "Ana", "usuario": "ana", "papel": "operador",
         "email": "ana@x.com", "ativo": True}])
    import gateway_auth
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(gateway_auth, "_chave", lambda t: chave.public_key())
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    import auth_routes
    app = FastAPI()
    app.middleware("http")(gateway_auth.middleware)

    @app.get("/api/frota/x")
    def frota(): return {"ok": 1}

    @app.get("/api/rh/x")
    def rh(u=Depends(auth_routes.usuario_logado)): return u

    @app.get("/rh/vaga/1")
    def vaga(): return {"publica": 1}

    def tok(**kw):
        base = {"iss": ISS, "azp": "intranet", "exp": time.time() + 300,
                "email": "ana@x.com", "preferred_username": "ana", "groups": []}
        base.update(kw)
        return {"X-Forwarded-Access-Token": jwt.encode(base, chave, algorithm="RS256")}
    return TestClient(app), tok


def test_publico_abre_sem_login(ctx):
    c, _ = ctx
    assert c.get("/rh/vaga/1").status_code == 200


def test_sem_token_401(ctx):
    c, _ = ctx
    assert c.get("/api/rh/x").status_code == 401


def test_token_forjado_ou_de_outro_cliente(ctx):
    c, tok = ctx
    assert c.get("/api/rh/x", headers={"X-Forwarded-Access-Token": "lixo"}).status_code == 401
    assert c.get("/api/rh/x", headers=tok(azp="monitoramento")).status_code == 401
    assert c.get("/api/rh/x", headers=tok(iss="http://outro/realms/x")).status_code == 401
    assert c.get("/api/rh/x", headers=tok(exp=time.time() - 5)).status_code == 401


def test_usuario_local_pelo_email(ctx):
    c, tok = ctx
    r = c.get("/api/rh/x", headers=tok())
    assert r.status_code == 200 and r.json()["usuario"] == "ana"
    assert c.get("/api/rh/x", headers=tok(email="ninguem@x.com")).status_code == 403


def test_grupo_por_app(ctx):
    c, tok = ctx
    assert c.get("/api/frota/x", headers=tok()).status_code == 403
    assert c.get("/api/frota/x", headers=tok(groups=["GS_App_Monitoramento"])).status_code == 403
    assert c.get("/api/frota/x", headers=tok(groups=["GS_App_Frota"])).status_code == 200
