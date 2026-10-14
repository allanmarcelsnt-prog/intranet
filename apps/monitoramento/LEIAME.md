# Monitoramento Safenet (shift-guardian)

App de **plantão e relatório semanal do monitoramento 24/7**. Registra, por cliente, os eventos de cada plantão (arme, desarme, disparo, manutenção, observação) e os bypasses de zona, e gera o relatório semanal, o arquivo mensal e as mensagens de WhatsApp a partir dos mesmos registros.

> Este arquivo complementa o `README.md` original (padrão do Lovable), `AGENTS.md` (decisões de projeto) e `deploy/README.md` (guia completo de instalação, em português). Documentado a partir da leitura do código em out/2026.

## 1. Visão geral

- **Origem:** projeto criado no Lovable. O `roadmap.md` registra a exportação para o GitHub (`lthinds/shift-guardian`, outra conta) e a adaptação para rodar na intranet **sem Lovable Cloud e sem internet**.
- **Relação com o app antigo:** foi desenhado para substituir o relatório semanal em FastAPI + JSON (`/api/monitoramento` no repositório raiz). A migração dos dados é feita por `deploy/legacy/import_monitoramento.py`, que lê `monitoramento.json` e `monitoramento_cadastros.json` e gera um SQL único em transação. Confirme no servidor qual dos dois está ativo em produção.
- **Hospedagem:** TrueNAS, Docker Compose, portas **8080** (HTTP) e **8443** (HTTPS).

## 2. Stack

| Camada | Tecnologia |
|---|---|
| Front/servidor | TanStack Start 1.168 (React 19, TypeScript 5.8), Tailwind 4, shadcn/ui, Vite 8 |
| Build / execução | Bun (build) → Node 22 alpine (execução), servidor standalone na porta 3000 |
| Banco | Postgres do Supabase 15.8 (`supabase/postgres:15.8.1.060`) com RLS |
| Login | GoTrue v2.186 (e-mail e senha) |
| API de dados | PostgREST v12.2.12 |
| Gateway | nginx 1.27 (única porta publicada; mesma origem para app, `/auth/v1` e `/rest/v1`) |
| Migrações | SQL em `drizzle/migrations`, aplicado pelo serviço `migrate` |
| Relatórios | PDF gerado no navegador (jsPDF), CSV e JSON de arquivo |
| Testes | Vitest |

## 3. Arquitetura

```
Navegador ──► gateway (nginx) ─┬─► app   (TanStack Start / Node)    /
   :8080 / :8443               ├─► auth  (GoTrue)                   /auth/v1/
                               └─► rest  (PostgREST + RLS)          /rest/v1/
                                        │
                                        ▼
                               db (Postgres) ◄── migrate (aplica drizzle/migrations e deploy/db/post)
```

- As **regras de acesso ficam no banco** (RLS e funções `security definer`), não no front.
- A chave pública (anon) chega ao navegador em tempo de execução por `/config.js`, gerado pelo nginx a partir do `.env`. A imagem do app não carrega segredos.
- `/auth/v1/admin/*` é bloqueado nas portas publicadas. O app só acessa essa API pelo listener interno do nginx (`SUPABASE_URL=http://gateway:8080`), usado para remover contas de operadores.

## 4. Telas (`src/routes`)

| Rota | Função |
|---|---|
| `/auth` | Login e cadastro de novo operador |
| `/plantao` | Registro dos eventos do plantão |
| `/relatorio` | Relatório semanal, exportação (PDF, CSV), escolha de pasta e arquivamento do mês |
| `/clientes` | Cadastro de clientes, usuários, dispositivos e sensores |
| `/campos` | Campos extras definidos pelo administrador, por tipo de evento |
| `/operadores` | Aprovação e remoção de operadores (administrador) |

A lógica pura de datas, relatório, mensagem de WhatsApp e CSV está em `src/lib/safenet.ts`, com testes.

## 5. Modelo de dados e permissões

**Tabelas (`public`):** `clients`, `client_users`, `client_devices`, `client_sensors`, `client_events`, `bypasses`, `custom_fields`, `shifts`, `profiles`, `user_roles`, `operator_access`.

- Todos os eventos do cliente (arme, desarme, disparo, manutenção, observação) ficam em **uma tabela**, `client_events`, distinguidos por `kind`. Bypasses têm tabela própria por terem início e fim.
- Cadastros são desativados (`active = false`), nunca apagados, e os eventos guardam o nome de sensores e itens no momento do registro. Renomear ou remover não altera o histórico.
- Campos extras (`custom_fields`) são guardados em `client_events.custom` (jsonb).

**Papéis:** `operator`, `manager` ("cadastros") e `admin`. `can_manage()` libera criar e editar cadastros; apagar é só do administrador.

**Aprovação de operadores:** quem se cadastra fica `pending` até um administrador aprovar (`operator_access`: `pending`, `approved`, `removed`). A RLS checa isso em todas as tabelas, então remover alguém bloqueia também sessões já abertas. O histórico do perfil permanece.

**Funções principais:** `has_role`, `can_manage`, `is_approved`, `set_operator_access`, `register_operator_access`, `prepare_archive_month`, `archive_month`, `confirm_saved_archive`, `purge_expired_archives`.

## 6. Arquivamento e retenção

- O administrador escolhe uma pasta (local ou de rede) em **Relatório semanal → Escolher pasta**. Isso exige HTTPS com certificado confiável (File System Access API); sem HTTPS, o app cai para download de arquivo.
- **Salvar e arquivar mês** grava um JSON completo com todos os clientes do mês. Só depois da confirmação os registros recebem `archived_at`.
- A retenção é de **um mês corrido a partir da confirmação do arquivo** (não da data do evento). Uma purga diária às **03:00 UTC** (`pg_cron`, agendada pelo serviço `migrate`) apaga somente registros arquivados e vencidos. Editar um registro arquivado o tira do arquivo e reinicia a retenção. Bypasses ativos nunca expiram.
- **Atenção:** o app apaga dados após o arquivamento. Confirme sempre que o JSON foi salvo antes de arquivar.

## 7. Instalação e operação

Resumo (guia completo em `deploy/README.md`):

```bash
./deploy/setup.sh http://<ip-do-servidor>:8080   # gera o .env com segredos aleatórios
docker compose up -d --build                      # compila o app, aplica as migrações, sobe tudo
./deploy/scripts/make-admin.sh email@empresa      # promove o primeiro administrador (após cadastrar na tela)
```

- **Backup:** `./deploy/scripts/backup.sh <pasta> <dias>` (pg_dump de `public` e `auth`), agendado no cron do TrueNAS. **Guarde também uma cópia do `.env`**: sem ele os logins existentes deixam de validar.
- **Restauração:** `./deploy/scripts/restore.sh <arquivo.dump>`, em uma única transação. Restaure em instalação da mesma versão do stack.
- **Atualizar o app:** substituir o código e `docker compose up -d --build app`. **Novas migrações:** adicionar `.sql` em `drizzle/migrations` (nunca editar uma já aplicada).
- **HTTPS:** colocar `fullchain.pem` e `privkey.pem` em `deploy/certs/` e definir `NGINX_CONF=nginx.https.conf` e `SITE_URL=https://...:8443` no `.env`.

### Variáveis (`.env`, gerado por `deploy/setup.sh`)

`SITE_URL`, `HTTP_PORT`, `HTTPS_PORT`, `NGINX_CONF`, `POSTGRES_PASSWORD`, `JWT_SECRET`, `ANON_KEY`, `SERVICE_ROLE_KEY`. Modelo em `.env.example`; os valores reais **nunca** vão para o repositório.

## 8. O que não está neste repositório (de propósito)

Na pasta do app no servidor existem itens que ficaram de fora:

- `.env` (senha do banco, `JWT_SECRET`, chave `service_role`).
- `deploy/certs/privkey.pem` e `fullchain.pem`, e o `safenet-monitoramento.crt` da pasta acima.
- A pasta `shift-guardian-backup-20261014` (cópia do mesmo código, anterior às últimas alterações) e os zips `projeto-fonte-v2` e `v5`. Com o código aqui, o repositório passa a ser a referência de versão.

## 9. Pontos de atenção

- **Cadastro aberto na rede:** `GOTRUE_DISABLE_SIGNUP=false` e `GOTRUE_MAILER_AUTOCONFIRM=true`. Qualquer pessoa que alcance o app cria uma conta, mas nada fica acessível sem aprovação do administrador (RLS restritiva). Vale revisar a lista em **Operadores** de tempos em tempos. Senha mínima de 6 caracteres.
- **Sem servidor de e-mail:** não há recuperação de senha. O fluxo é o administrador excluir o cadastro e a pessoa se cadastrar de novo, ou redefinir via banco.
- **Perda do `.env` = perda dos logins.** Mantenha cópia segura, fora do repositório.
- **Restos do Lovable:** `.lovable/` e `src/lib/lovable-error-reporting.ts` só chamam ganchos que existem no editor do Lovable; pela leitura, não fazem chamadas de rede. Podem ser removidos em uma limpeza futura.
- **PDF do relatório semanal** é texto simples (jsPDF). A paridade visual com o app antigo está pendente no `roadmap.md`.
- Exemplos de IP e host (`192.168.1.50`, `monitoramento.safenet.local`) aparecem só em documentação e scripts de exemplo.
