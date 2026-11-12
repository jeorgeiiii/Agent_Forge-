# AgentForge web - production build (next build + next start). Build from the repo root:
#   docker build -f apps/web/Dockerfile --build-arg API_PROXY_URL=http://api:8000 -t agentforge-web .
# API_PROXY_URL is where the web server forwards /api/* - it is baked in at build time.

FROM node:22-alpine AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
ARG API_PROXY_URL=http://api:8000
ENV API_PROXY_URL=$API_PROXY_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev

FROM node:22-alpine
WORKDIR /app
ARG API_PROXY_URL=http://api:8000
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 API_PROXY_URL=$API_PROXY_URL
COPY --from=build --chown=node:node /app/package.json /app/next.config.mjs ./
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/.next ./.next
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:3000/login || exit 1
CMD ["npx", "next", "start", "-p", "3000"]
