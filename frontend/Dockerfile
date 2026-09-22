# syntax=docker/dockerfile:1.7
# Alpha Direct Finance — Next.js frontend
# Multi-stage build using Next standalone output for a tiny runtime image.

# -----------------------------------------------------------------------------
# Stage 1: deps (cache npm install)
# -----------------------------------------------------------------------------
FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# -----------------------------------------------------------------------------
# Stage 2: builder
# -----------------------------------------------------------------------------
FROM node:22-bookworm-slim AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# NEXT_PUBLIC_* env must be present at build time — Next.js bakes them into
# the client bundle. Pass via docker-compose build args; safe to leave blank
# (SSO simply unavailable; login page falls back to password).
ARG NEXT_PUBLIC_AZURE_TENANT_ID=""
ARG NEXT_PUBLIC_AZURE_CLIENT_ID=""
ARG NEXT_PUBLIC_AZURE_API_SCOPE=""
ENV NEXT_PUBLIC_AZURE_TENANT_ID=$NEXT_PUBLIC_AZURE_TENANT_ID \
    NEXT_PUBLIC_AZURE_CLIENT_ID=$NEXT_PUBLIC_AZURE_CLIENT_ID \
    NEXT_PUBLIC_AZURE_API_SCOPE=$NEXT_PUBLIC_AZURE_API_SCOPE

COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Generate ONE build id and use it everywhere: next.config reads OMNI_BUILD_ID
# for both generateBuildId (.next/BUILD_ID) and the client bundle
# (NEXT_PUBLIC_BUILD_ID), and we write the SAME value to public/build-id.txt.
# The id is what already-open clients (browser tabs + the Windows OmniDesktop
# app) poll at /build-id.txt to detect a new deploy and offer a refresh.
# It MUST be set here, not left to next.config's fallback: next.config is
# evaluated more than once per build, so a randomBytes() fallback drew a
# different value for the bundle vs the file — a permanent false "refresh"
# banner. Fixing OMNI_BUILD_ID in the environment makes every read identical.
RUN OMNI_BUILD_ID="$(node -e 'process.stdout.write(require("crypto").randomBytes(8).toString("hex"))')" \
    && OMNI_BUILD_ID="$OMNI_BUILD_ID" npm run build \
    && printf '%s' "$OMNI_BUILD_ID" > public/build-id.txt

# -----------------------------------------------------------------------------
# Stage 3: runtime (non-root, minimal)
# -----------------------------------------------------------------------------
FROM node:22-bookworm-slim AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 nodejs \
    && useradd  --system --uid 1001 --gid nodejs --home-dir /app --shell /usr/sbin/nologin nextjs

COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:3000/login > /dev/null || exit 1

CMD ["node", "server.js"]
