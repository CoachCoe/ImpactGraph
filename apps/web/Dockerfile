# syntax=docker/dockerfile:1
# Build context is the repository root. next.config.ts loads env from two levels up, and
# NEXT_PUBLIC_* values are inlined at build time -- so the API URL is a build argument,
# not a runtime variable.
FROM node:22-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web ./
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ARG NEXT_PUBLIC_RPC_URL=
ARG NEXT_PUBLIC_BLOCK_EXPLORER_URL=
ARG NEXT_PUBLIC_CHAIN_ID=
ARG NEXT_PUBLIC_CHAIN_NAME=
ARG NEXT_PUBLIC_IMPACT_REGISTRY_ADDRESS=
# Only ever set for a disposable demo chain; the component refuses to render on a public
# network regardless.
ARG NEXT_PUBLIC_DEMO_VERIFIER_ADDRESS=
ARG NEXT_PUBLIC_DEMO_VERIFIER_KEY=
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_PUBLIC_RPC_URL=$NEXT_PUBLIC_RPC_URL \
    NEXT_PUBLIC_BLOCK_EXPLORER_URL=$NEXT_PUBLIC_BLOCK_EXPLORER_URL \
    NEXT_PUBLIC_CHAIN_ID=$NEXT_PUBLIC_CHAIN_ID \
    NEXT_PUBLIC_CHAIN_NAME=$NEXT_PUBLIC_CHAIN_NAME \
    NEXT_PUBLIC_IMPACT_REGISTRY_ADDRESS=$NEXT_PUBLIC_IMPACT_REGISTRY_ADDRESS \
    NEXT_PUBLIC_DEMO_VERIFIER_ADDRESS=$NEXT_PUBLIC_DEMO_VERIFIER_ADDRESS \
    NEXT_PUBLIC_DEMO_VERIFIER_KEY=$NEXT_PUBLIC_DEMO_VERIFIER_KEY \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -g 10001 nextjs && adduser -u 10001 -G nextjs -S nextjs
COPY --from=build --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nextjs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD node -e "require('http').get('http://127.0.0.1:3000/',r=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))"
CMD ["node", "server.js"]
