# The dashboard's image: the pages built once, then served by nginx, which also hands every
# /api/... request on to the dispatch server. docker-compose.yml runs it as `web`.

# Stage 1: build the pages (type check, then Vite) into dist/.
FROM node:22-alpine AS build
WORKDIR /dashboard

# The packages first, in their own layer: reinstalled only when the lock file changes.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

# Stage 2: nginx and the built pages only. Node and the packages stay behind in stage 1.
FROM nginx:1.28-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /dashboard/dist /usr/share/nginx/html

EXPOSE 80

# Answers when nginx is up: what `docker compose ps` shows as healthy.
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
