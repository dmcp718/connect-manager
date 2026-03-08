# CONNECT Manager

Web app for importing S3 objects into LucidLink filespaces via External Data Store API.

## Branches
- **main**: Single-user mode, no authentication
- **multi-user**: Multi-user with JWT auth, admin/user roles, Caddy HTTPS
- **aws-deploy**: AWS deployment (Terraform + deploy.sh) with local LucidLink API container

## Stack
- **Backend**: FastAPI + Python 3.12, ARQ workers, Valkey (Redis-compatible)
- **Frontend**: HTMX + Alpine.js, custom CSS (LucidLink brand)
- **Auth** (multi-user): JWT tokens, bcrypt passwords, per-user data isolation
- **Secrets**: Fernet encryption (AES-128) at rest, key derived from JWT_SECRET_KEY
- **Services**: web (:8000), worker x2, valkey (:6379), lucidlink-api (:3003), caddy (:443 prod)

## Key Files
**Backend**: `app/main.py` (routes), `app/services/lucidlink.py` (API client), `app/services/s3_service.py` (boto3), `app/services/worker.py` (ARQ + SQS polling), `app/services/database.py` (SQLite)

**Auth** (multi-user): `app/services/auth.py`, `app/services/user_state.py`, `app/routes/auth.py`, `app/middleware/auth.py`

**Frontend**: `app/templates/base.html` (layout), `app/templates/partials/*.html` (tabs/modals), `app/static/css/style.css`

**AWS Deployment**: `deploy.sh` (CLI wrapper), `docker-compose.aws.yml` (EFS volumes + lucidlink-api), `terraform/` (infrastructure)

## Development
```bash
./run.sh              # Start (HTTP :8000)
./run.sh build        # Rebuild
./run.sh logs         # View logs
```

## Production (multi-user branch)
**IMPORTANT: This server uses a shared external Caddy. Always use `prod-shared` commands.**

```bash
# Configure .env
DOMAIN=connect.example.com
JWT_SECRET_KEY=<openssl rand -hex 32>
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=secure-password

# Shared Caddy setup (THIS IS WHAT WE USE)
./run.sh prod-shared       # Start (external Caddy handles HTTPS)
./run.sh prod-shared-build # Rebuild
./run.sh prod-logs         # View logs

# Alternative: Built-in Caddy (NOT used - conflicts with shared Caddy on :443)
# ./run.sh prod
# ./run.sh prod-build
```

## AWS Deployment (aws-deploy branch)
EC2 + ASG (1/1/1) with ALB, EFS, and local `lucidlink/lucidlink-api` container. ~$37/month.

```bash
./deploy.sh setup      # Interactive config → SSM secrets + terraform.tfvars
./deploy.sh plan       # Preview infrastructure
./deploy.sh deploy     # Deploy infrastructure + upload app
./deploy.sh status     # Instance health + ALB target status
./deploy.sh ssh        # SSM session (no SSH keys)
./deploy.sh logs       # App logs | ./deploy.sh logs boot
./deploy.sh update     # Push code changes to running instance
./deploy.sh secrets    # List/rotate SSM secrets
./deploy.sh destroy    # Full teardown
```

**Architecture**: ALB (HTTPS/TLS 1.3) → EC2 t3.small (Docker Compose) → EFS (/data)
**Services**: web, worker x4, valkey, lucidlink-api (Docker network: `http://lucidlink-api:3003/api/v1`)
**Secrets**: SSM Parameter Store (JWT, admin creds, domain). No SSH keys — SSM only.
**Compose overlay**: `docker-compose.aws.yml` adds lucidlink-api + EFS bind mounts

## API Endpoints
```
POST /api/load-filespaces              # Load filespaces + DataStores
POST /api/create-datastore             # Create S3 DataStore
GET|DELETE /api/datastores/{id}/info   # DataStore info/delete
POST|DELETE /api/datastores/{id}/credentials  # S3 browsing creds
GET /api/browse/{datastore_id}         # Browse S3 bucket
POST /api/import/file|folder           # Import to LucidLink
GET /api/jobs, POST /api/jobs/{id}/cancel, DELETE /api/jobs/{id}
POST|DELETE /api/sqs/credentials       # SQS IAM creds
POST|DELETE /api/sqs/queues            # SQS queue config
```

## Auth Endpoints (multi-user)
```
POST /api/auth/login                   # Login, returns JWT cookie
POST /api/auth/logout                  # Logout, clears cookie
POST /api/auth/change-password         # Change password
GET /api/tab/account                   # Account settings + user management
POST /api/auth/users/invite            # Create user (admin)
DELETE /api/auth/users/{id}            # Delete user (admin)
```

## Brand Guidelines
- **Colors**: Charcoal (#151519), Neon (#B0FB15), Indigo (#5E53E0)
- **Purple theme**: Accent (#A366C6), Hover (#BD90D6), Success (#4ADE80), Text (#EBE8E0)
- **Typography**: Aeonik (headings), Inter (body)
- **Icons**: Lucide SVG, **Case**: Sentence case only

## Theme System
- CSS-only via `[data-theme="purple"]` selector overriding `:root` variables
- Toggle in header (`base.html`), persisted to `localStorage` key `theme`
- Anti-flash `<script>` in `base.html` and `login.html` applies theme before render
- Purple logo: `app/static/img/lab_flask_purp.svg`
- Hardcoded `rgba(176,251,21,...)` values need explicit purple overrides (not covered by CSS variable remapping)
- No backend involvement — purely client-side

## AWS IAM Actions
S3 bucket notification actions (do NOT add "Configuration" suffix):
- `s3:GetBucketNotification` (not GetBucketNotificationConfiguration)
- `s3:PutBucketNotification` (not PutBucketNotificationConfiguration)
