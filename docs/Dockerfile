# Stage 1: Build the static site
FROM python:3.13-slim AS builder

RUN pip install --no-cache-dir mkdocs-material

WORKDIR /build
COPY mkdocs.yml .
COPY guide/ guide/

RUN mkdocs build

# Stage 2: Serve with Caddy
FROM caddy:2-alpine

COPY --from=builder /build/site /srv
COPY Caddyfile /etc/caddy/Caddyfile
