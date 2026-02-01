# CONNECT Manager

Web app for importing S3 objects into LucidLink filespaces via External Data Store API.

## Branches
- **main**: Single-user mode, no authentication
- **multi-user**: Multi-user with JWT auth, admin/user roles, Caddy HTTPS

## Stack
- **Backend**: FastAPI + Python 3.12, ARQ workers, Valkey (Redis-compatible)
- **Frontend**: HTMX + Alpine.js, custom CSS (LucidLink brand)
- **Auth** (multi-user): JWT tokens, bcrypt passwords, per-user data isolation
- **Services**: web (:8000), worker x2, valkey (:6379), caddy (:443 prod)

## Key Files
**Backend**: `app/main.py` (routes), `app/services/lucidlink.py` (API client), `app/services/s3_service.py` (boto3), `app/services/worker.py` (ARQ + SQS polling), `app/services/database.py` (SQLite)

**Auth** (multi-user): `app/services/auth.py`, `app/services/user_state.py`, `app/routes/auth.py`, `app/middleware/auth.py`

**Frontend**: `app/templates/base.html` (layout), `app/templates/partials/*.html` (tabs/modals), `app/static/css/style.css`

## Development
```bash
./run.sh              # Start (HTTP :8000)
./run.sh build        # Rebuild
./run.sh logs         # View logs
```

## Production (multi-user branch)
```bash
# Configure .env
DOMAIN=connect.example.com
JWT_SECRET_KEY=<openssl rand -hex 32>
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=secure-password

# Start with Caddy reverse proxy
./run.sh prod         # HTTPS :443
./run.sh prod-build   # Rebuild prod
./run.sh prod-logs    # View logs
```

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
- **Typography**: Aeonik (headings), Inter (body)
- **Icons**: Lucide SVG, **Case**: Sentence case only
