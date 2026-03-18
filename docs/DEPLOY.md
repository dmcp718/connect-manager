# Deploying the Integration Guide

Deploy the LucidLink Connect Integration Guide as a static site behind the shared Caddy reverse proxy.

**URL:** https://wrkflw-guide.solutions-eng.online

---

## Prerequisites

- Docker and Docker Compose installed on the server
- Shared Caddy network (`caddy_shared`) already running
- DNS A record for `wrkflw-guide.solutions-eng.online` pointing to the server's public IP

## Architecture

```
Internet → :443 (shared Caddy + auto-HTTPS) → wrkflw-guide:8080 → static files from /srv
```

| Component | Details |
|-----------|---------|
| Web server | Caddy 2 (Alpine), plain HTTP on :8080 |
| TLS | Handled by shared Caddy (Let's Encrypt) |
| Content | MkDocs Material static HTML (built at image build time) |
| Crawler blocking | robots.txt + meta tags + X-Robots-Tag header + bot UA filter |
| Network | `caddy_shared` (external Docker network) |

## Initial deployment

### 1. Clone the repository

```bash
ssh user@your-server
git clone git@bitbucket.org:lucidlink/lucidlink-connect-web-app.git
cd lucidlink-connect-web-app/docs
```

### 2. Add to shared Caddy

Add this block to the shared Caddyfile:

```
wrkflw-guide.solutions-eng.online {
    reverse_proxy wrkflw-guide:8080
}
```

Reload shared Caddy:

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

### 3. Build and start

```bash
docker compose up -d --build
```

This will:
- Build the MkDocs static site inside a container
- Start Caddy to serve it on port 8080 (HTTP only)
- Join the `caddy_shared` network so the shared Caddy can reach it

### 4. Verify

```bash
# Check container is running
docker compose ps

# Check logs
docker compose logs guide

# Test HTTPS (via shared Caddy)
curl -I https://wrkflw-guide.solutions-eng.online
```

You should see a `200` response with `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet`.

## Updating content

After editing any markdown files in `docs/guide/`:

```bash
cd lucidlink-connect-web-app/docs
git pull
docker compose up -d --build
```

The rebuild takes about 10 seconds. No TLS impact — the shared Caddy keeps its certificates.

## Local development

Run locally on port 8080 (no shared Caddy needed):

```bash
docker compose up -d --build
open http://127.0.0.1:8080
```

## Changing the domain

1. Create a DNS A record for the new domain
2. Update the shared Caddyfile with the new domain
3. Reload shared Caddy:

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

## Troubleshooting

### Container won't start

```bash
# Check if port 8080 is already in use
ss -tlnp | grep :8080

# Check container logs
docker compose logs --tail 50 guide
```

### Shared Caddy can't reach the container

```bash
# Verify container is on the caddy_shared network
docker network inspect caddy_shared | grep wrkflw-guide

# Test from shared Caddy
docker exec caddy wget -qO- http://wrkflw-guide:8080 | head -5
```

### Force rebuild (no cache)

```bash
docker compose build --no-cache
docker compose up -d
```

## Stopping the site

```bash
docker compose down
```
