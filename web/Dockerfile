# Frontend image: the Next.js chat app + CopilotKit runtime route handlers.
#
# Build context is ./web. Produces a Next.js *standalone* server (see
# next.config.ts `output: "standalone"`) so the runtime image ships only the
# traced node_modules, not the full dependency tree.

FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# `npm install` (not `npm ci`): the CopilotKit/markdown transitive graph triggers
# a known npm install-vs-ci ideal-tree disagreement, so `npm ci`'s strict sync
# check rejects an otherwise-valid lockfile. `npm install` reconciles against the
# committed lock and installs deterministically enough for this build.
RUN npm install --no-audit --no-fund

FROM node:22-bookworm-slim AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Ensure a public/ dir exists so the standalone copy below always succeeds, even
# before any static assets are added.
RUN mkdir -p public && npm run build

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Run as the unprivileged user the node image already provides.
USER node
COPY --from=builder --chown=node:node /app/public ./public
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
EXPOSE 3000
# BACKEND_URL is read at request time by the route handlers (server-side), so it
# is supplied at runtime by compose — not baked into the build.
CMD ["node", "server.js"]
