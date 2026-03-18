# Deploying the Integration Guide

Deploy the LucidLink Connect Integration Guide as a static site with automatic HTTPS.

**URL:** https://wrkflw-guide.solutions-eng.online

---

## Prerequisites

- Docker and Docker Compose installed on the server
- Ports 80 and 443 open (firewall / port forwarding)
- DNS A record for `wrkflw-guide.solutions-eng.online` pointing to the server's public IP

## Initial deployment

### 1. Clone the repository

```bash
ssh user@your-server
git clone git@bitbucket.org:lucidlink/lucidlink-connect-web-app.git
cd lucidlink-connect-web-app/docs
```

### 2. Build and start

```bash
docker compose up -d --build
```

This will:
- Build the MkDocs static site inside a container
- Start Caddy to serve it on ports 80/443
- Automatically provision a Let's Encrypt TLS certificate

### 3. Verify

```bash
# Check container is running
docker compose ps

# Check logs for successful TLS provisioning
docker compose logs guide

# Test HTTPS
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

The rebuild takes about 10 seconds. Caddy restarts with zero downtime and reuses the existing TLS certificate from the `caddy_data` volume.

## Architecture

```
Internet → :443 (Caddy + auto-HTTPS) → static files from /srv
```

| Component | Details |
|-----------|---------|
| Web server | Caddy 2 (Alpine) |
| TLS | Let's Encrypt, auto-renewed |
| Content | MkDocs Material static HTML (built at image build time) |
| Crawler blocking | robots.txt + meta tags + X-Robots-Tag header + bot UA filter |

### Docker volumes

| Volume | Purpose |
|--------|---------|
| `caddy_data` | TLS certificates (persists across rebuilds) |
| `caddy_config` | Caddy runtime config |

## Changing the domain

1. Create a DNS A record for the new domain
2. Edit `docker-compose.yml` — change the `DOMAIN` environment variable
3. Rebuild:

```bash
docker compose up -d --build
```

Caddy will automatically provision a new certificate for the new domain.

## Troubleshooting

### Certificate not provisioning

```bash
docker compose logs guide | grep -i "tls\|cert\|acme"
```

Common causes:
- Port 80 not reachable from the internet (needed for ACME HTTP challenge)
- DNS not yet propagated — verify with `dig wrkflw-guide.solutions-eng.online`
- Firewall blocking inbound 80/443

### Container won't start

```bash
# Check if ports are already in use
ss -tlnp | grep -E ':80|:443'

# Check container logs
docker compose logs --tail 50 guide
```

### Force rebuild (no cache)

```bash
docker compose build --no-cache
docker compose up -d
```

### View Caddy config at runtime

```bash
docker compose exec guide caddy list-modules
docker compose exec guide cat /etc/caddy/Caddyfile
```

## Stopping the site

```bash
docker compose down
```

To also remove TLS certificates:

```bash
docker compose down -v
```
