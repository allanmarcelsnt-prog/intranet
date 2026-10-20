# Intranet Safenet

Backend e páginas da intranet da Safenet Tecnologia / REGGRA. Um único serviço FastAPI serve o portal (HTML estático) e as APIs dos módulos internos: licitações, RH (recrutamento e avaliação de desempenho), frota e monitoramento.

> Documento gerado a partir da leitura do código em out/2026. Onde algo foi deduzido do código e não testado em produção, está indicado.

## 1. Visão geral

- **Backend:** FastAPI (`main.py`), um processo, sem banco de dados. Toda a persistência é em arquivos JSON na pasta `data/`.
- **Front-end:** páginas HTML estáticas em `static/`, servidas pelo próprio FastAPI (`StaticFiles` montado em `/`).
- **Hospedagem:** container Docker (`intranet-safenet`) em um TrueNAS. O dataset da aplicação fica em `/mnt/Safenet_Storage/intranet`. Acesso interno por `intranet.safenet.lan` (DNS próprio no container `safenet-dns`).
- **Padrão "Caminho A":** fonte de dados (planilha, RSS) → script Python → JSON em `data/` → FastAPI → HTML.

## 2. Stack

| Item | Versão / detalhe |
|---|---|
| Python | 3.12 (imagem `python:3.12-slim`) |
| FastAPI / Uvicorn | 0.115.6 / 0.34.0 |
| Autenticação | PyJWT 2.10.1 (cookie HttpOnly) + bcrypt 4.2.1 |
| Planilhas | openpyxl 3.1.5 |
| PDFs | pdfplumber 0.11.9 (extração de itens de edital) |
| Upload | python-multipart 0.0.20 |
| E-mail | `smtplib` (biblioteca padrão) |
| IA (triagem de candidatos) | API Anthropic via `urllib`, sem SDK |

## 3. Estrutura do repositório

```
main.py                          Ponto de entrada: registra os routers e monta o site estático
auth_routes.py / auth_store.py   Autenticação central, papéis e gestão de usuários
licitacoes_routes.py             Montagem de proposta (fluxo de pregões) — maior módulo
rh_routes.py                     Gestor de recrutamento e seleção
desempenho_routes.py             Avaliação de desempenho (ciclos, autoavaliação, supervisor)
desempenho_routes_fatia_b.py     Versão antiga/parcial do módulo acima (não é importada)
frota_routes.py                  Relatório de frota
monitoramento_routes.py          Relatório semanal de monitoramento
edital_extractor.py              Extrai itens de editais em PDF
gerar_json*.py                   Geradores de dados (comercial, notícias, treinamentos)
seed*.py, migrar_fatia1_rh.py    Carga inicial e migrações pontuais
monitor_dns.sh                   Monitor do DNS interno (roda no host TrueNAS)
static/                          index.html (portal), licitacoes/, rh/, treinamentos/, frota/, monitoramento/
apps/                            Apps independentes com código e deploy próprios (ver apps/README.md)
  monitoramento/                 shift-guardian: plantão e relatório semanal do monitoramento (TanStack Start + Postgres)
```

Ficam fora do repositório (ver `.gitignore`): `data/` (dados de RH, usuários, licitações, PDFs), `cloudflared`/`.cloudflared/`, backups (`*.bak`, `*.old*`) e os builds compilados de `static/frota/assets` e `static/monitoramento/assets`.

## 4. Como roda

```
uvicorn main:app --host 0.0.0.0 --port 80      # dentro do container
```

- Ordem importa em `main.py`: todos os routers precisam ser registrados **antes** de `app.mount("/", StaticFiles(...))`, senão o site estático captura as rotas `/api/...`.
- `GET /health` responde `{"status": "ok"}`. Documentação interativa em `/api/docs`.
- A pasta `data/` é criada automaticamente na subida.

## 5. Autenticação e papéis

- Login por usuário e senha em `POST /api/auth/login`. Gera um JWT (HS256, validade de 7 dias) guardado no cookie `intranet_sessao` (HttpOnly, SameSite=Lax).
- Usuários em `data/intranet_usuarios.json`: `{id, nome, usuario, papel, papeis_extras, email, senha_hash, ativo}`. Senhas com bcrypt. No primeiro acesso `senha_hash` é nulo e a pessoa define a própria senha (`/api/auth/definir-senha`). O administrador pode zerar a senha de qualquer usuário (exceto a própria).
- Dependência `usuario_logado` (em `auth_routes.py`) é o que os módulos usam para exigir sessão e gravar quem agiu (base da auditoria).
- **Papel principal** (`papel`): `analista`, `analista_cotacao`, `comercial`, `atribuidor`, `operacional`, `coordenador`.
- **Papéis de RH** (`papeis_extras`): `rh_operador`, `rh_coordenador`, `rh_tecnico`, `rh_solicitante`. Aditivos: não alteram o papel principal. Coordenadores têm acesso total ao RH por herança.
- **Administrador:** o login definido em `INTRANET_ADMIN_LOGIN` (padrão `allan`) acessa o gerenciamento de usuários. Qualquer `coordenador` pode editar e-mails de contato.

### Ponto de integração para login único com o AD

A troca fica concentrada em dois lugares:

1. `auth_store.verificar_senha(usuario, senha)`: hoje compara com o hash bcrypt local. Pode passar a fazer o bind LDAP no AD e, em caso de sucesso, mapear grupos do AD para `papel` / `papeis_extras`.
2. `auth_routes._gerar_token` / `usuario_logado`: o restante (cookie, JWT, dependências dos módulos) pode permanecer como está.

O identificador do usuário em todo o sistema é o campo `usuario` (login). O ideal é que ele coincida com o `sAMAccountName` do AD.

## 6. Módulos

| Módulo | Prefixo | Arquivo | Rotas | Exige login |
|---|---|---|---|---|
| Autenticação | `/api/auth` | `auth_routes.py` | 16 | parcial (login e lista de usuários são públicos) |
| Licitações | `/api/licitacoes` | `licitacoes_routes.py` | 49 | sim |
| RH — recrutamento | `/api/rh` | `rh_routes.py` | 47 | sim (candidatura pública por token é exceção) |
| RH — desempenho | `/api/rh/desempenho` | `desempenho_routes.py` | 27 | sim (links por token são públicos) |
| Frota | `/api/frota` | `frota_routes.py` | 14 | **não** |
| Monitoramento | `/api/monitoramento` | `monitoramento_routes.py` | 17 | **não** |
| Dados vivos | `/api/dados/{nome}` | `main.py` | 1 | **não** |

### Licitações (montagem de proposta)

Fluxo do pregão: `triagem` → `aguardando_adriano` (validação) → `aguardando_rodrigo` (atribuição) → `definir_sugestoes` → `em_cotacao` → `em_revisao` → `pronto_effecti` → `em_disputa` → `ganho` / `perdido` / `nao_realizado`.

- Cada etapa pertence a um papel; o coordenador pode agir em qualquer uma. Toda ação grava o usuário autenticado na auditoria.
- Funções: anotações, documentos (upload/download), devolução e retrocesso de etapa, arquivar/restaurar, cotação com exportação para Excel, datasheets, entrega em ZIP, lições aprendidas, histórico.
- `edital_extractor.py` lê o PDF do edital e extrai a tabela de itens (especificação, quantidade, valor unitário).
- **SLA diário:** uma thread (`sla-diario`) avisa por e-mail quando um pregão fica parado em validação por 3 dias ou uma devolução fica 1 dia sem correção. É idempotente (`licitacoes_sla_log.json`).

### RH — recrutamento

- Vagas, candidatos e kanban com as etapas `triagem` → `selecao` → `entrevista_social` → `entrevista_tecnica` → `aprovado`.
- Triagem por IA: o currículo é enviado à API da Anthropic (modelo fixo na constante `ANTHROPIC_MODEL` de `rh_routes.py`) e o resultado passa por validação humana.
- Banco de talentos, onboarding com trilhas e tarefas, estatísticas, anúncio da vaga gerado e editável.
- Formulário público de candidatura por token (sem login), em `public_router`.
- Thread `sla-rh` com avisos de prazo.

### RH — avaliação de desempenho

- Cadastro de colaboradores (carga inicial em `seed_colaboradores.py`) e critérios técnicos por setor.
- Ciclo de avaliação: o sistema envia links com token para autoavaliação, avaliação do avaliador e co-avaliação do supervisor (todos públicos, sem login). Depois encerra o ciclo e calcula o resultado.
- Gatilhos automáticos rodando em thread própria (a cada 24 h), com log em `desempenho_gatilhos_log.json`.

### Frota e Monitoramento

- Frota: veículo → dia, mais cadastros (membros, locais, classificações, cargos). Dados em `frota.json` e `frota_cadastros.json`.
- Monitoramento: três eixos (`semanas`, `acervo`, cadastros), porque pontos de atenção atravessam semanas. Relatório semanal consulta o acervo por sobreposição de período.
- As telas em `static/frota` e `static/monitoramento` são builds compilados (Vite/React). **O código-fonte dessas telas antigas não está neste repositório**; só o `index.html` de cada uma.
- O **novo app de monitoramento** (shift-guardian), que roda em Docker com banco próprio e foi desenhado para substituir o relatório semanal acima, tem o código-fonte em [`apps/monitoramento`](apps/monitoramento/LEIAME.md). A migração dos dados do app antigo usa `apps/monitoramento/deploy/legacy/import_monitoramento.py`.
- O **login único** (Keycloak, OIDC, acesso por app, AD numa fase seguinte) tem o pacote de implantação em [`apps/keycloak`](apps/keycloak/LEIAME.md).

## 7. Dados

Persistência em JSON dentro de `data/`, no mesmo padrão em todos os módulos: `_ler` / `_escrever_atomico` (arquivo temporário + `os.replace`) / lock de thread / ids sequenciais.

| Grupo | Arquivos |
|---|---|
| Usuários | `intranet_usuarios.json` |
| Licitações | `licitacoes_pregoes`, `_auditoria`, `_historico`, `_licoes`, `_email_log`, `_sla_log` |
| RH recrutamento | `rh_vagas`, `rh_candidatos`, `rh_cargos`, `rh_banco_talentos`, `rh_onboarding`, `rh_auditoria`, `rh_email_log`, `rh_sla_log` |
| Desempenho | `desempenho_colaboradores`, `_criterios`, `_ciclos`, `_gatilhos_log`, `_email_log` |
| Frota | `frota`, `frota_cadastros` |
| Monitoramento | `monitoramento`, `monitoramento_cadastros` |
| Portal | `comercial`, `noticias`, `treinamentos` |
| Arquivos | `data/documentos/` (editais), `data/datasheets/` |

O padrão (JSON + lock de thread) só é seguro com **um único processo** do Uvicorn. Com mais de um worker, as escritas podem se sobrepor.

## 8. Variáveis de ambiente

| Variável | Uso | Padrão no código |
|---|---|---|
| `INTRANET_JWT_SECRET` | Assinatura do JWT | `"troque-este-segredo-em-producao"` |
| `INTRANET_ADMIN_LOGIN` | Login do administrador | `allan` |
| `INTRANET_SMTP_HOST` / `_PORT` / `_USER` / `_PASS` | Envio de e-mail | vazio (e-mail desligado); porta 587 em licitações, 465 em RH e desempenho |
| `INTRANET_SMTP_STARTTLS` | STARTTLS | `true` em licitações, `false` em RH e desempenho |
| `INTRANET_SMTP_FROM` / `_FROM_NOME` | Remetente | usuário SMTP / nome do módulo |
| `INTRANET_BASE_URL` | Base dos links enviados por e-mail | `http://intranet.safenet.lan:8081/licitacoes/` (licitações), `http://192.168.0.171:8081` (desempenho) |
| `RH_PUBLIC_BASE_URL` | Base dos links públicos do RH | cai em `INTRANET_BASE_URL` |
| `ANTHROPIC_API_KEY` | Triagem por IA no RH | vazio (triagem desligada) |
| `PASTA_COMERCIAL` / `SAIDA_JSON` | Entrada e saída de `gerar_json.py` | `/data/comercial` |

Nenhuma credencial está gravada no código.

## 9. Rotinas agendadas e scripts

| Script | Onde roda | Frequência |
|---|---|---|
| `gerar_json.py` (dashboard comercial) | `docker exec intranet-safenet ...` | cron do TrueNAS |
| `gerar_json_noticias.py` (RSS) | cron da WebUI do TrueNAS | de hora em hora |
| `gerar_json_treinamentos.py` | `docker exec intranet-safenet ...` | cron do TrueNAS |
| `monitor_dns.sh` | host TrueNAS (fora do container) | a cada 5 min |
| `seed.py`, `seed_colaboradores.py`, `migrar_fatia1_rh.py` | uma vez | manual |

`monitor_dns.sh` valida a resolução de `intranet.safenet.lan`, alerta por e-mail e, após 2 falhas seguidas, reinicia o container `safenet-dns` (no máximo 1 vez a cada 30 min).

## 10. Pontos de atenção

Itens observados na leitura do código. Os de segurança foram deduzidos do código e **não foram testados no ambiente em execução**.

### Segurança (prioridade alta)

1. **`GET /api/dados/{nome}` não exige login e serve qualquer `data/<nome>.json`.** Pelo código, isso inclui `intranet_usuarios` (e-mails e hashes bcrypt), `rh_candidatos`, `desempenho_colaboradores` e `licitacoes_*`. Correção sugerida: lista fixa de nomes públicos (`comercial`, `noticias`, `treinamentos`) e `usuario_logado` para o resto.
2. **Frota e Monitoramento não têm autenticação**, inclusive nas rotas que gravam e apagam dados (`POST`/`DELETE`). Correção sugerida: `Depends(usuario_logado)` nos dois routers.
3. `GET /api/auth/usuarios` é público e lista nome, login e papel de todos os usuários ativos (serve à tela de login, mas facilita enumeração).
4. Sessão: o cookie não é marcado `Secure`; o JWT vale 7 dias e `usuario_logado` não reconsulta `ativo`, então um usuário desativado continua com acesso até o token expirar; não há limite de tentativas de login; senha mínima de 6 caracteres.
5. Se `INTRANET_JWT_SECRET` não estiver definida no servidor, o segredo cai no valor padrão público do código, e qualquer um poderia forjar sessões. Confirme que está definida.

### Técnicos

- **O `Dockerfile` está desatualizado:** copia apenas `main.py`, `gerar_json.py`, `frota_routes.py` e `static/`, mas o `main.py` importa também `auth_routes`, `auth_store`, `licitacoes_routes`, `rh_routes`, `desempenho_routes` e `monitoramento_routes`. Um build limpo falharia na importação. Em produção, provavelmente o código é montado por volume; vale conferir.
- `desempenho_routes_fatia_b.py` não é importado em lugar nenhum e duplica rotas do módulo principal. Candidato a remoção.
- Em `rh_routes.py` há duas rotas `POST /admin/sla/rodar-agora` declaradas.
- O `main.py` e os módulos grandes (`licitacoes_routes.py` com ~3.000 linhas, `rh_routes.py` com ~3.000) concentram regra de negócio, rotas e acesso a dados no mesmo arquivo. Funciona, mas dificulta testes e a futura troca de JSON por banco.
- A triagem por IA envia dados de candidatos a um serviço externo; vale alinhar com as regras de LGPD da empresa.
- O IP interno `192.168.0.171` aparece fixo em `monitor_dns.sh` e em um valor padrão do módulo de desempenho.
