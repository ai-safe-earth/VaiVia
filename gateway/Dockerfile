# The gateway: the only public service (auth, rate limits, origin control,
# SSE proxy). Build from the REPO ROOT, like the other images:
#   docker build -f gateway/Dockerfile -t vaivia-gateway .
#
# NODE_ENV=production is set by the production overlay, not here, so the
# same image refuses GATEWAY_DEV_NO_AUTH and a missing secret/JWKS URL there
# (gateway/src/config.ts) and can still be run locally for a check.

FROM node:22-alpine AS build
WORKDIR /app
COPY gateway/package.json gateway/package-lock.json ./
RUN npm ci
COPY gateway/ ./
RUN npm run build

FROM node:22-alpine
WORKDIR /app
COPY gateway/package.json gateway/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist

USER node
EXPOSE 3001
CMD ["node", "dist/server.js"]
