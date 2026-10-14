# syntax=docker/dockerfile:1
# Monitoramento Safenet — imagem do app (TanStack Start, servidor Node standalone).

FROM oven/bun:1-alpine AS build
WORKDIR /app
COPY package.json bun.lock bunfig.toml ./
RUN bun install --frozen-lockfile
COPY . .
# A imagem é genérica: URL (mesma origem) e chave pública chegam em tempo de execução (/config.js via nginx).
RUN bun run build

FROM public.ecr.aws/docker/library/node:22-alpine
ENV NODE_ENV=production PORT=3000 HOST=0.0.0.0
WORKDIR /app
COPY --from=build /app/.output ./.output
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1:3000/auth || exit 1
CMD ["node", ".output/server/index.mjs"]
