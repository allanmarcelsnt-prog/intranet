# Apps da intranet

Aplicações independentes da intranet Safenet que têm código-fonte e implantação próprios. O backend principal (FastAPI) fica na raiz do repositório; ver o `README.md` de lá.

| App | Pasta | O que é | Documentação |
|---|---|---|---|
| Monitoramento (shift-guardian) | `monitoramento/` | Plantão e relatório semanal do monitoramento 24/7 (TanStack Start + Postgres/GoTrue/PostgREST em Docker) | [`monitoramento/LEIAME.md`](monitoramento/LEIAME.md) |

## Regras para novos apps

- Um app por subpasta, com o próprio `.gitignore`.
- Nunca versionar `.env`, chaves privadas (`*.pem`, `*.key`), certificados, dumps de banco ou pastas de backup. Deixar um `.env.example` com os nomes das variáveis.
- Documentar em português: o que é, stack, como instalar, variáveis, backup e pontos de atenção.
