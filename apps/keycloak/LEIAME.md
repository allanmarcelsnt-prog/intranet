# Keycloak — login único da intranet Safenet

Provedor de identidade (IdP) central. Cada app da intranet vira um *client* OIDC e o usuário só entra nos apps em que tem permissão. Usuários locais primeiro; federação com o Active Directory depois (ver `docs/FEDERACAO-AD.md`).

## Como funciona o acesso por app

- Realm `safenet`. Cada app tem um client com o mesmo nome (`monitoramento`, `frota`...), um papel `acesso` e um grupo `GS_App_<Slug>`.
- Cada app tem um fluxo de login próprio (`acesso-<slug>`): depois de validar senha **ou cookie de SSO**, verifica se o usuário tem o papel `acesso` do app. Sem o papel, aparece "Você não tem acesso". Isso vale também para quem já tem sessão aberta em outro app.
- Conceder ou revogar = `kc.py conceder` / `kc.py revogar` (ou colocar o usuário no grupo `GS_App_<Slug>`, que é o que o AD fará no futuro).

Testado localmente (Keycloak 26.8.1 sem Docker): ana só no Monitoramento, beto só na Frota, carla em nenhum; bloqueio também com cookie de SSO; token traz `email`, `email_verified` e `resource_access.<app>.roles = [acesso]`.
**Não testado:** `docker compose` real e o caminho com Postgres. Faça o piloto antes de depender disso.

## Instalação (no shell do TrueNAS, pasta nova, ex.: `/mnt/<pool>/apps/keycloak`)

```bash
cd keycloak
./deploy/setup.sh http://192.168.0.171:8180   # gera .env com senhas aleatórias (chmod 600)
docker compose up -d
docker compose logs -f keycloak               # aguarde "Listening on"
```

Portas: **8180** (HTTP) e **8543** (HTTPS). Não conflita com 80/443 (TrueNAS), 8080/8443 (Monitoramento) e 8081 (backend da intranet).
Painel: `http://192.168.0.171:8180/admin/` — usuário/senha em `.env` (`KC_BOOTSTRAP_ADMIN_*`). Depois do primeiro acesso crie um admin nominal e remova o temporário. **Guarde uma cópia do `.env` no cofre de senhas** (nunca no Git).

## Cadastrar apps e usuários

O script usa só Python 3 (stdlib). Lê `KEYCLOAK_URL` e a senha do admin do `.env`.

```bash
python3 deploy/scripts/kc.py app monitoramento --nome "Monitoramento" \
    --redirect "http://<url-do-monitoramento>/auth/v1/callback" --mostrar-secret
python3 deploy/scripts/kc.py usuario ana --email ana@safenettecnologia.com.br --nome "Ana Souza" --grupo monitoramento
python3 deploy/scripts/kc.py conceder ana frota
python3 deploy/scripts/kc.py revogar  ana frota
python3 deploy/scripts/kc.py listar
```

`usuario` gera senha temporária (troca obrigatória no 1º login) e marca o e-mail como verificado (necessário para vincular contas existentes pelo e-mail). O `--mostrar-secret` imprime o client secret uma única vez — vai direto para o `.env` do app, nunca para o Git.

## Integrar um app Supabase (Monitoramento / Frota)

No `.env` do GoTrue do app (**só em ambiente de teste primeiro**):

```
GOTRUE_EXTERNAL_KEYCLOAK_ENABLED=true
GOTRUE_EXTERNAL_KEYCLOAK_CLIENT_ID=monitoramento
GOTRUE_EXTERNAL_KEYCLOAK_SECRET=<secret do kc.py>
GOTRUE_EXTERNAL_KEYCLOAK_REDIRECT_URI=<SITE_URL>/auth/v1/callback
GOTRUE_EXTERNAL_KEYCLOAK_URL=http://192.168.0.171:8180/realms/safenet
```

Mantenha `GOTRUE_DISABLE_SIGNUP=false` (o OAuth precisa criar o usuário). No front, um botão "Entrar com a conta Safenet" chamando `supabase.auth.signInWithOAuth({ provider: 'keycloak', scopes: 'openid' })`, ao lado do login atual. Prompt pronto para o Lovable em `docs/PROMPT-LOVABLE.md`. O login antigo só é removido no fim da migração.

## HTTPS

Em HTTP funciona na LAN (IP privado), mas senha trafega sem criptografia e a pasta "Escolher pasta" do Monitoramento exige HTTPS. Com certificado da CA interna: coloque `fullchain.pem` e `privkey.pem` em `deploy/certs/` (ignorados pelo Git) e suba com
`docker compose -f docker-compose.yml -f docker-compose.https.yml up -d`. A emissão depende do AD CS — **adiada, só com autorização do Allan**.

## Backup

```bash
./deploy/scripts/backup.sh /mnt/<pool>/backups/keycloak 14   # pg_dump + retenção; agende no TrueNAS (Cron Job)
./deploy/scripts/restore.sh <arquivo.dump>                    # pede confirmação "SIM"
```

Faça uma cópia do `.env` junto. Restaure sempre na mesma versão do Keycloak.

## Pontos de atenção do piloto

- Com HTTP, o Keycloak só aceita bem acesso por IP privado (192.168.x); por `localhost` o cookie de login falha.
- Atualização de versão: trocar a tag em `docker-compose.yml` após backup.
- Se o Keycloak cair, quem tem sessão aberta nos apps continua; novos logins pelo botão SSO falham (por isso o login antigo fica até o fim).
- Política de senha: 10+ caracteres, diferente do usuário; bloqueio após 5 falhas.

## Tema da tela de login (Safenet / REGGRA)

Pasta `themes/safenet` (montada em `/opt/keycloak/themes/safenet` pelo `docker-compose.yml`). Herda o tema padrão e troca cores, logo e textos (`messages/`). Em instalação nova o realm já vem com `loginTheme: safenet`; em instalação existente: painel → Realm settings → Themes → Login theme = `safenet` → Save. Em modo produção o Keycloak guarda o tema em cache: após alterar arquivos, `docker compose up -d --force-recreate keycloak`.
