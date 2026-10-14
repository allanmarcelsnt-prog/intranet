# Monitoramento Safenet — implantação na intranet (TrueNAS / Docker)

Este stack roda o app **sem Lovable Cloud e sem internet**: tudo em containers na própria TrueNAS.

```
Navegador ──► gateway (nginx) ─┬─► app    (TanStack Start / Node)      /
   :8080 / :8443               ├─► auth   (GoTrue — login e senha)     /auth/v1/
                               └─► rest   (PostgREST — dados + RLS)    /rest/v1/
                                        │
                                        ▼
                               db (Postgres do Supabase) ◄── migrate (aplica o SQL de drizzle/migrations)
```

- As **regras de acesso continuam no banco** (RLS): operador, cadastros, administrador e aprovação de novos operadores.
- Só o **gateway** é publicado (portas 8080 e 8443). Banco, login e API ficam na rede interna do Docker.
- O app e o navegador falam com a **mesma origem** (sem CORS). A chave pública chega por `/config.js`, gerado pelo nginx a partir do `.env`; por isso a imagem do app é igual em qualquer instalação.

## Requisitos

- TrueNAS com Docker e `docker compose` (testado com Docker 29 / Compose v2).
- ~5 GB livres para as imagens (a do Postgres do Supabase tem cerca de 3 GB) e espaço para o banco e os backups.
- Acesso à internet **somente** na primeira instalação (baixar imagens e compilar o app). Sem internet na TrueNAS: veja _Instalação offline_.
- `openssl` e `bash` no shell da TrueNAS (já vêm no sistema).

## Instalação (primeira vez)

No Shell da TrueNAS, dentro de um dataset (exemplo: `/mnt/<pool>/apps/safenet-monitoramento`):

```bash
# 1) coloque aqui o conteúdo do projeto (zip do código-fonte) e entre na pasta
cd /mnt/<pool>/apps/safenet-monitoramento

# 2) gera o .env com senhas e chaves aleatórias. Use o endereço que os operadores vão digitar.
./deploy/setup.sh http://192.168.1.50:8080

# 3) compila o app e sobe tudo (a primeira vez demora alguns minutos)
docker compose up -d --build

# 4) confira: todos "healthy"/"Up"; o serviço "migrate" termina com "Exited (0)" — é o esperado
docker compose ps -a
```

Acesse `http://<ip-da-truenas>:8080`.

> Portas **8080/8443** foram escolhidas para não colidir com a interface web da TrueNAS (80/443). Mude `HTTP_PORT`/`HTTPS_PORT` no `.env` se precisar.

### Criar o primeiro administrador

Cadastros novos ficam **pendentes** até um administrador aprovar (e o primeiro administrador precisa ser criado manualmente):

1. Na tela de login clique em **Novo operador? Cadastre-se** e cadastre o seu usuário.
2. No Shell da TrueNAS:
   ```bash
   ./deploy/scripts/make-admin.sh seu.email@safenettecnologia.com.br
   ```
3. Na tela "Seu cadastro aguarda aprovação" clique em **Verificar autorização**. Daqui em diante, aprove os demais operadores pela tela **Operadores**.

> Não há confirmação por e-mail (a intranet não tem servidor de e-mail). O controle de acesso é a aprovação do administrador.

## HTTPS (necessário para "Escolher pasta" no Relatório semanal)

O navegador só libera a escolha de pasta (File System Access API) em **HTTPS com certificado confiável**. Em `http://ip` o app funciona, mas o relatório cai no modo "baixar arquivo".

1. Obtenha um certificado da **CA da empresa** para o nome/IP do servidor (preferível) — ou gere um autoassinado e instale-o como confiável nos PCs dos operadores:
   ```bash
   ./deploy/scripts/make-selfsigned.sh monitoramento.safenet.local   # ou o IP
   ```
   Se usar certificado próprio, copie `fullchain.pem` e `privkey.pem` para `deploy/certs/`.
2. No `.env`: `NGINX_CONF=nginx.https.conf` e `SITE_URL=https://monitoramento.safenet.local:8443`.
3. `docker compose up -d --force-recreate gateway`

Com HTTPS ativo, a porta HTTP redireciona para a HTTPS.

## Backup e restauração

```bash
./deploy/scripts/backup.sh /mnt/<pool>/backups/safenet 14   # pasta destino, dias de retenção
```

Agende essa linha em **Tarefas → Cron Jobs** da TrueNAS (por exemplo, todo dia às 02:00). O backup inclui dados **e** contas de login. Faça também **cópia do `.env`** (sem ele não é possível validar logins existentes) e, se quiser, snapshots do dataset.

Restaurar (substitui os dados atuais; a operação é atômica — se falhar, nada muda):

```bash
./deploy/scripts/restore.sh /mnt/<pool>/backups/safenet/safenet-AAAAMMDD-HHMMSS.dump
```

Em servidor novo: instale normalmente (`up -d --build`), copie o **mesmo `.env`** do servidor antigo **antes** de subir e então restaure. Restaure sempre numa instalação da mesma versão do stack que gerou o backup.

## Atualizações

- **Novo código do app:** substitua os arquivos do projeto e rode `docker compose up -d --build app`.
- **Novas migrations:** coloque os `.sql` novos em `drizzle/migrations/`; o serviço `migrate` aplica os que faltam, uma vez cada, a cada `docker compose up -d`. **Não edite** migrations que já foram aplicadas.
- **Versões das imagens** (Postgres, GoTrue, PostgREST) estão fixadas em `docker-compose.yml`; atualize com cuidado e faça backup antes.

## Retenção de arquivos mensais

A purga diária (03:00 UTC) roda dentro do banco com `pg_cron`, agendada automaticamente pelo `migrate`. Conferir:

```bash
docker compose exec -T db bash -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h localhost -U supabase_admin -d postgres -c "select jobname, schedule from cron.job"'
```

## Instalação offline (TrueNAS sem internet)

Num PC com internet e Docker:

```bash
docker compose build app
docker pull supabase/postgres:15.8.1.060 nginx:1.27-alpine
docker pull public.ecr.aws/supabase/gotrue:v2.186.0 public.ecr.aws/supabase/postgrest:v12.2.12
docker save -o safenet-imagens.tar safenet-monitoramento-app:latest supabase/postgres:15.8.1.060 nginx:1.27-alpine \
  public.ecr.aws/supabase/gotrue:v2.186.0 public.ecr.aws/supabase/postgrest:v12.2.12
```

Na TrueNAS: `docker load -i safenet-imagens.tar` e depois `docker compose up -d --no-build`.

## Segurança — o que foi feito

- `/auth/v1/admin/*` (painel administrativo do GoTrue) é **bloqueado** nas portas publicadas. O app acessa essa API só pela porta interna 8080 do nginx, que não é publicada — é assim que "Excluir cadastro" remove a conta do operador.
- O `.env` contém segredos (senha do banco, `JWT_SECRET`, chave `service_role`): permissão 600, **não versionar** (está no `.gitignore`).
- As funções `has_role`, `can_manage` e `is_approved` não são executáveis pelo papel anônimo (`deploy/db/post/001-hardening.sql`).
- Cadastro é aberto a quem alcançar o app na rede, mas **nada é acessível sem aprovação** do administrador (RLS restritiva em todas as tabelas).

## Problemas comuns

| Sintoma | O que verificar |
| --- | --- |
| `docker compose up` reclama de `POSTGRES_PASSWORD` | Rode `./deploy/setup.sh` para gerar o `.env`. |
| `migrate` termina com erro | `docker compose logs migrate` — o erro aponta o arquivo `.sql`. Corrija e rode `docker compose up -d` de novo (as migrations já aplicadas são puladas). |
| Tela de login abre mas não entra | `docker compose logs auth`; confirme que `auth` está healthy e que o `.env` não foi trocado depois da primeira subida. |
| "Escolher pasta" não aparece/funciona | Falta HTTPS com certificado confiável (seção HTTPS). |
| Mudou o `.env` e nada mudou | `docker compose up -d --force-recreate` (e `docker compose build app` se mudou código). |
| Esqueci a senha de um operador | Um administrador pode excluir e o operador se cadastrar de novo; ou redefina via banco com o GoTrue (peça ajuda ao TI). |

## Migração dos dados do app antigo

O importador lê `monitoramento.json` e `monitoramento_cadastros.json` (pasta `data` do app antigo) e gera um SQL único (transação). **Nada é gravado até você aplicar o SQL.**

```bash
# 1) prévia + geração do SQL (qualquer máquina com Python 3)
python3 deploy/legacy/import_monitoramento.py monitoramento.json monitoramento_cadastros.json -o import-legado.sql

# 2) FAÇA BACKUP antes (deploy/scripts/backup.sh), depois aplique:
docker compose exec -T db bash -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h localhost -U supabase_admin -d postgres -v ON_ERROR_STOP=1 -1' < import-legado.sql
```

O SQL se recusa a rodar duas vezes (clientes importados levam `[legado:CÓDIGO]` nas observações). Para refazer, apague os clientes legado pela tela de Clientes ou restaure o backup.

Como os dados foram mapeados:

| App antigo | Novo app |
| --- | --- |
| cliente (código, endereço, cidade, CEP, responsável) | cliente (endereço montado; código e responsável nas observações) |
| usuários / dispositivos / sensores | cadastros do cliente (dispositivos globais viram um conjunto por cliente; local e obs do sensor vão no nome) |
| armes / desarmes | eventos arme/desarme (`Monitoramento Reggra` = feito pelo operador) |
| disparos (incl. contínuos) | eventos de disparo, sensores vinculados pelo nome (contínuo: início/fim) |
| zonas com motivo | bypass encerrado no mesmo dia |
| zona "Fechada" | observação |
| visitas | evento de manutenção (equipe, veículo, recebido por, relato no texto) |
| observações da semana, pontos de atenção, anormalidades técnicas | eventos de observação com prefixo `[Obs. semanal]`, `[Ponto de atenção]`, `[Anormalidade técnica]` |

Atenção: o novo app **arquiva e apaga** registros após o arquivamento mensal. O histórico importado não vem arquivado, mas entra no ciclo normal quando o admin arquivar o mês.
