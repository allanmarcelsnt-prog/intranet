#!/usr/bin/env python3
"""
kc.py — administra o Keycloak da Safenet (realm "safenet") pela API de administração.
Só biblioteca padrão (Python 3.8+). Pode rodar na TrueNAS ou em qualquer PC da rede.

Comandos:
  app <slug> --nome "Monitoramento" --redirect <URL> [--redirect <URL>...]
        Cria (ou atualiza) um app: cliente OIDC, papel "acesso", grupo GS_App_<Slug>
        e o fluxo de login que BARRA quem não pertence ao grupo.
  usuario <login> --email <e-mail> --nome "Nome Sobrenome" [--grupo <slug>...]
        Cria um usuário local com senha temporária (troca obrigatória no 1º acesso).
  conceder <login> <slug>     Dá acesso a um app (coloca o usuário no grupo do app).
  revogar  <login> <slug>     Tira o acesso a um app.
  listar                      Mostra, por app, quem tem acesso.

Conexão: --url (ou KEYCLOAK_URL no ambiente / .env), administrador via KC_ADMIN_USER
e KC_ADMIN_PASSWORD (ou o admin inicial do .env; se não servir, pergunta a senha).
Exemplo:
  python3 deploy/scripts/kc.py app monitoramento --nome Monitoramento \\
      --redirect http://192.168.0.171:8080/auth/v1/callback
"""
import argparse
import getpass
import json
import os
import re
import secrets
import ssl
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REALM = "safenet"
ROLE = "acesso"
PASTA = Path(__file__).resolve().parent.parent.parent  # apps/keycloak
PREFIXO_GRUPO = "GS_App_"


class ErroKC(Exception):
    pass


def ler_env() -> dict:
    env = {}
    arq = PASTA / ".env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                env[k.strip()] = v.strip()
    return env


class KC:
    def __init__(self, url: str, insecure: bool = False):
        self.url = url.rstrip("/")
        self.ctx = ssl.create_default_context()
        if insecure:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        self.token = None
        self._cred = None

    # ---- baixo nível ----
    def _req(self, metodo, caminho, corpo=None, form=False, auth=True):
        headers = {}
        dados = None
        if corpo is not None:
            if form:
                dados = urllib.parse.urlencode(corpo).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                dados = json.dumps(corpo).encode()
                headers["Content-Type"] = "application/json"
        if auth and self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.url + caminho, data=dados, method=metodo, headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
                bruto = r.read()
                return r.status, (json.loads(bruto) if bruto else None), r.headers
        except urllib.error.HTTPError as e:
            bruto = e.read()
            try:
                corpo_err = json.loads(bruto) if bruto else None
            except ValueError:
                corpo_err = bruto.decode("utf-8", "replace")[:300]
            return e.code, corpo_err, e.headers
        except urllib.error.URLError as e:
            raise ErroKC(f"Não consegui conectar em {self.url}: {e.reason}")

    def login(self, usuario: str, senha: str):
        self._cred = (usuario, senha)
        st, corpo, _ = self._req(
            "POST", "/realms/master/protocol/openid-connect/token",
            {"grant_type": "password", "client_id": "admin-cli", "username": usuario, "password": senha},
            form=True, auth=False)
        if st != 200:
            raise ErroKC("Login de administrador recusado (usuário ou senha incorretos).")
        self.token = corpo["access_token"]

    def _admin(self, metodo, caminho, corpo=None, ok=(200, 201, 204)):
        st, resp, hdr = self._req(metodo, f"/admin/realms/{REALM}{caminho}", corpo)
        if st == 401 and self._cred:  # token do admin dura ~1 min: renova uma vez
            self.login(*self._cred)
            st, resp, hdr = self._req(metodo, f"/admin/realms/{REALM}{caminho}", corpo)
        if st not in ok:
            raise ErroKC(f"{metodo} {caminho} -> HTTP {st}: {resp}")
        return st, resp, hdr

    def get(self, caminho):
        return self._admin("GET", caminho)[1]

    def post(self, caminho, corpo, ok=(200, 201, 204)):
        return self._admin("POST", caminho, corpo, ok)

    def put(self, caminho, corpo=None):
        return self._admin("PUT", caminho, corpo)

    def delete(self, caminho):
        return self._admin("DELETE", caminho)


def id_do_location(hdr) -> str:
    return hdr.get("Location", "").rstrip("/").rsplit("/", 1)[-1]


def q(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def nome_grupo(slug: str) -> str:
    return PREFIXO_GRUPO + "".join(p.capitalize() for p in slug.split("-"))


def validar_slug(slug: str):
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        raise ErroKC("O slug do app deve ter só letras minúsculas, números e hífen (ex.: monitoramento).")


# ---------------------------------------------------------------- clientes / grupos
def achar_cliente(kc: KC, slug: str):
    r = kc.get(f"/clients?clientId={q(slug)}")
    return r[0] if r else None


def achar_grupo(kc: KC, nome: str):
    for g in kc.get(f"/groups?search={q(nome)}&exact=true"):
        if g["name"] == nome:
            return g
    return None


def garantir_grupo(kc: KC, nome: str) -> str:
    g = achar_grupo(kc, nome)
    if g:
        return g["id"]
    _, _, hdr = kc.post("/groups", {"name": nome})
    return id_do_location(hdr)


# ---------------------------------------------------------------- fluxo de login por app
def criar_fluxo(kc: KC, slug: str) -> str:
    """Fluxo de login do app: (cookie SSO OU usuário/senha) e, depois, barra quem não tem
    o papel <slug>.acesso. A checagem vem DEPOIS do login/cookie, então vale também para
    quem já tem sessão aberta por outro app."""
    top = f"acesso-{slug}"
    existente = next((f for f in kc.get("/authentication/flows") if f["alias"] == top), None)
    if existente:
        return existente["id"]

    sub_login, sub_form, sub_check = f"login-{slug}", f"formulario-{slug}", f"checagem-{slug}"
    _, _, hdr = kc.post("/authentication/flows", {
        "alias": top, "description": f"Login do app {slug}: só entra quem tem o papel {slug}.{ROLE}",
        "providerId": "basic-flow", "topLevel": True, "builtIn": False})
    top_id = id_do_location(hdr)
    try:
        def sub(pai, alias):
            kc.post(f"/authentication/flows/{q(pai)}/executions/flow",
                    {"alias": alias, "type": "basic-flow", "provider": "registration-page-form",
                     "description": ""})

        def execu(pai, provider):
            kc.post(f"/authentication/flows/{q(pai)}/executions/execution", {"provider": provider})

        sub(top, sub_login)
        sub(top, sub_check)
        execu(sub_login, "auth-cookie")
        sub(sub_login, sub_form)
        execu(sub_form, "auth-username-password-form")
        execu(sub_check, "conditional-user-role")
        execu(sub_check, "deny-access-authenticator")

        def requisitos(alias, regras):
            lista = kc.get(f"/authentication/flows/{q(alias)}/executions")
            for ex in lista:
                # subfluxo é identificado pelo nome (displayName); peça, pelo providerId
                chave = ex.get("displayName") if ex.get("authenticationFlow") else ex.get("providerId")
                if chave in regras:
                    ex["requirement"] = regras[chave]
                    kc.put(f"/authentication/flows/{q(alias)}/executions", ex)

        requisitos(top, {sub_login: "REQUIRED", sub_check: "CONDITIONAL"})
        requisitos(top, {"auth-cookie": "ALTERNATIVE", sub_form: "ALTERNATIVE",
                         "auth-username-password-form": "REQUIRED",
                         "conditional-user-role": "REQUIRED", "deny-access-authenticator": "REQUIRED"})

        # configurações das duas peças da checagem
        lista = kc.get(f"/authentication/flows/{q(top)}/executions")
        for ex in lista:
            if ex.get("providerId") == "conditional-user-role":
                kc.post(f"/authentication/executions/{ex['id']}/config", {
                    "alias": f"cond-role-{slug}",
                    "config": {"condUserRole": f"{slug}.{ROLE}", "negate": "true"}})
            elif ex.get("providerId") == "deny-access-authenticator":
                kc.post(f"/authentication/executions/{ex['id']}/config", {
                    "alias": f"negar-{slug}",
                    "config": {"denyErrorMessage": "Você não tem acesso a este aplicativo. Fale com o TI."}})
        return top_id
    except Exception:
        try:
            kc.delete(f"/authentication/flows/{top_id}")  # não deixa fluxo pela metade
        except ErroKC:
            pass
        raise


# ---------------------------------------------------------------- comandos
def cmd_app(kc: KC, a):
    validar_slug(a.slug)
    origens = sorted({"{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(u)) for u in a.redirect})
    rep = {
        "clientId": a.slug, "name": a.nome or a.slug, "protocol": "openid-connect",
        "enabled": True, "publicClient": False, "standardFlowEnabled": True,
        "implicitFlowEnabled": False, "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False, "redirectUris": a.redirect, "webOrigins": origens,
        "attributes": {"post.logout.redirect.uris": "+"},
    }
    cli = achar_cliente(kc, a.slug)
    novo = cli is None
    if novo:
        _, _, hdr = kc.post("/clients", rep)
        cid = id_do_location(hdr)
    else:
        cid = cli["id"]
        cli.update({k: rep[k] for k in ("name", "redirectUris", "webOrigins")})
        kc.put(f"/clients/{cid}", cli)

    # papel "acesso" do app + grupo GS_App_<Slug> que concede esse papel
    st, _, _ = kc._admin("POST", f"/clients/{cid}/roles",
                         {"name": ROLE, "description": f"Pode usar o app {a.nome or a.slug}"},
                         ok=(201, 409))
    papel = kc.get(f"/clients/{cid}/roles/{ROLE}")
    gnome = nome_grupo(a.slug)
    gid = garantir_grupo(kc, gnome)
    kc.post(f"/groups/{gid}/role-mappings/clients/{cid}", [papel])

    # fluxo que barra quem não tem o papel, ligado a este cliente
    fluxo_id = criar_fluxo(kc, a.slug)
    cli = kc.get(f"/clients/{cid}")
    cli["authenticationFlowBindingOverrides"] = {"browser": fluxo_id}
    kc.put(f"/clients/{cid}", cli)

    print(f"App '{a.slug}' {'criado' if novo else 'atualizado'}.")
    print(f"  Grupo de acesso : {gnome}")
    print(f"  Papel           : {a.slug}.{ROLE}")
    print(f"  Redirecionamento: {', '.join(a.redirect)}")
    if novo or a.mostrar_secret:
        seg = kc.get(f"/clients/{cid}/client-secret")["value"]
        print(f"  Client ID       : {a.slug}")
        print(f"  Client Secret   : {seg}")
        print("  (guarde o secret no .env do app; ele também aparece no console do Keycloak)")


def achar_usuario(kc: KC, login: str):
    for u in kc.get(f"/users?username={q(login)}&exact=true"):
        return u
    return None


def cmd_usuario(kc: KC, a):
    if not re.fullmatch(r"[a-z0-9._-]{2,40}", a.login):
        raise ErroKC("Login deve ter 2-40 caracteres: minúsculas, números, ponto, hífen ou underline.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a.email):
        raise ErroKC("E-mail inválido.")
    if achar_usuario(kc, a.login):
        raise ErroKC(f"O usuário '{a.login}' já existe.")
    partes = a.nome.strip().split(None, 1)
    senha = a.senha or "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    grupos = []
    for slug in a.grupo or []:
        g = achar_grupo(kc, nome_grupo(slug))
        if not g:
            raise ErroKC(f"Não existe o grupo do app '{slug}'. Crie o app antes (comando 'app').")
        grupos.append(g["id"])
    _, _, hdr = kc.post("/users", {
        "username": a.login, "email": a.email.lower(), "firstName": partes[0],
        "lastName": partes[1] if len(partes) > 1 else "", "enabled": True,
        "emailVerified": True,  # o e-mail confirmado permite ligar à conta antiga dos apps
        "requiredActions": ["UPDATE_PASSWORD"]})
    uid = id_do_location(hdr)
    kc.put(f"/users/{uid}/reset-password", {"type": "password", "value": senha, "temporary": True})
    for gid in grupos:
        kc.put(f"/users/{uid}/groups/{gid}")
    print(f"Usuário '{a.login}' criado.")
    print(f"  Senha temporária: {senha}   (troca obrigatória no primeiro login)")
    print(f"  Acesso a: {', '.join(a.grupo) if a.grupo else '(nenhum app ainda)'}")


def _usuario_e_grupo(kc: KC, login: str, slug: str):
    u = achar_usuario(kc, login)
    if not u:
        raise ErroKC(f"Usuário '{login}' não encontrado.")
    g = achar_grupo(kc, nome_grupo(slug))
    if not g:
        raise ErroKC(f"Não existe o grupo do app '{slug}'.")
    return u["id"], g["id"]


def cmd_conceder(kc: KC, a):
    uid, gid = _usuario_e_grupo(kc, a.login, a.slug)
    kc.put(f"/users/{uid}/groups/{gid}")
    print(f"'{a.login}' agora tem acesso a '{a.slug}'.")


def cmd_revogar(kc: KC, a):
    uid, gid = _usuario_e_grupo(kc, a.login, a.slug)
    kc.delete(f"/users/{uid}/groups/{gid}")
    print(f"'{a.login}' não tem mais acesso a '{a.slug}'. Sessões já abertas valem até expirarem.")


def cmd_listar(kc: KC, a):
    grupos = [g for g in kc.get("/groups?search=" + q(PREFIXO_GRUPO)) if g["name"].startswith(PREFIXO_GRUPO)]
    if not grupos:
        print("Nenhum app cadastrado ainda.")
    for g in sorted(grupos, key=lambda x: x["name"]):
        membros = kc.get(f"/groups/{g['id']}/members?max=1000")
        nomes = ", ".join(sorted(m["username"] for m in membros)) or "(ninguém)"
        print(f"{g['name']}: {nomes}")


# ---------------------------------------------------------------- main
def main():
    env = ler_env()
    p = argparse.ArgumentParser(description="Administra o Keycloak da Safenet.")
    p.add_argument("--url", default=os.environ.get("KEYCLOAK_URL") or env.get("KEYCLOAK_URL"),
                   help="Endereço do Keycloak, ex.: http://192.168.0.171:8180")
    p.add_argument("--admin-user", default=os.environ.get("KC_ADMIN_USER")
                   or env.get("KC_BOOTSTRAP_ADMIN_USERNAME") or "admin")
    p.add_argument("--insecure", action="store_true", help="Não validar o certificado HTTPS (só para teste).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("app", help="Cria/atualiza um app")
    s.add_argument("slug")
    s.add_argument("--nome")
    s.add_argument("--redirect", action="append", required=True, help="URL de retorno do login (repetível)")
    s.add_argument("--mostrar-secret", action="store_true", help="Mostra o secret de um app já existente")
    s.set_defaults(fn=cmd_app)

    s = sub.add_parser("usuario", help="Cria um usuário local")
    s.add_argument("login")
    s.add_argument("--email", required=True)
    s.add_argument("--nome", required=True)
    s.add_argument("--grupo", action="append", help="slug do app a liberar (repetível)")
    s.add_argument("--senha", help="Senha temporária (padrão: gera uma aleatória)")
    s.set_defaults(fn=cmd_usuario)

    for nome, fn in (("conceder", cmd_conceder), ("revogar", cmd_revogar)):
        s = sub.add_parser(nome, help=f"{nome.capitalize()} acesso a um app")
        s.add_argument("login")
        s.add_argument("slug")
        s.set_defaults(fn=fn)

    s = sub.add_parser("listar", help="Lista quem tem acesso a cada app")
    s.set_defaults(fn=cmd_listar)

    a = p.parse_args()
    if not a.url:
        p.error("Informe --url ou defina KEYCLOAK_URL (no ambiente ou no .env).")
    kc = KC(a.url, a.insecure)
    try:
        senha = os.environ.get("KC_ADMIN_PASSWORD") or env.get("KC_BOOTSTRAP_ADMIN_PASSWORD")
        try:
            if not senha:
                raise ErroKC("sem senha")
            kc.login(a.admin_user, senha)
        except ErroKC as e:
            if "Não consegui conectar" in str(e):
                raise
            if not sys.stdin.isatty():
                raise ErroKC("Defina KC_ADMIN_PASSWORD (a senha do administrador do Keycloak).")
            kc.login(a.admin_user, getpass.getpass(f"Senha do administrador '{a.admin_user}': "))
        a.fn(kc, a)
    except ErroKC as e:
        print(f"Erro: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
