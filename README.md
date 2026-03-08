# LucidLink Labs: CONNECT Manager

A web application for importing S3 objects into LucidLink filespaces using the External Data Store API.

## Branches

| Branch | Description |
|--------|-------------|
| `main` | Multi-user with JWT authentication, admin/user roles, production-ready |
| `multi-user` | Legacy multi-user branch (merged to main) |
| `aws-deploy` | AWS deployment with Terraform, ALB, EFS, and local LucidLink API container |

## Features

- **DataStore Management** - Create, view, and delete S3 DataStores in LucidLink
- **S3 Browser** - Navigate and explore S3 buckets with per-card pagination (50 items/page default)
- **Bulk Import** - Import individual files or entire folders with parallel processing
- **AWS SQS Event Stream** - Automatic imports triggered by S3 event notifications
- **Job Queue** - Track import progress with real-time updates
- **Activity Logs** - Real-time SSE-based logging

### Additional Features
- **User Authentication** - JWT-based login with secure httponly cookies
- **Role-Based Access** - Admin and standard user roles
- **User Management** - Admins can add/remove users
- **Per-User Data Isolation** - Each user's DataStore credentials are private
- **Encrypted Secrets** - AWS credentials encrypted at rest (Fernet AES-128)
- **Theme Toggle** - Neon (default) and warm purple themes, persisted to localStorage
- **Production Deployment** - Caddy reverse proxy with automatic HTTPS
- **AWS Deployment** - Terraform + deploy.sh with ALB, ASG auto-recovery, EFS persistence

## Quick Start

### Docker (Recommended)

**macOS / Linux:**
```bash
./run.sh
```

**Windows:**
```cmd
run.bat
```

Open http://localhost:8000 in your browser.

### Available Commands

| Command | Description |
|---------|-------------|
| `start` | Start the application (default) |
| `stop` | Stop the application |
| `restart` | Restart the application |
| `logs` | Show application logs |
| `status` | Show container status |
| `build` | Rebuild and start |
| `clean` | Stop and remove all containers/volumes |
| `help` | Show usage information |

**Production Commands (multi-user branch):**

| Command | Description |
|---------|-------------|
| `prod` | Start with Caddy reverse proxy (HTTPS) |
| `prod-build` | Rebuild and start production |
| `prod-stop` | Stop production deployment |
| `prod-logs` | Show production logs |

**Production with External Reverse Proxy:**

| Command | Description |
|---------|-------------|
| `prod-shared` | Start without Caddy (use external proxy) |
| `prod-shared-build` | Rebuild and start (external proxy mode) |
| `prod-shared-stop` | Stop external proxy deployment |
| `prod-shared-logs` | Show logs (external proxy mode) |

Example: `./run.sh logs` or `run.bat restart`

### Manual Setup (Development)

Requires [uv](https://docs.astral.sh/uv/) and a running Valkey/Redis instance.

```bash
cd app
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Note: Manual setup requires `VALKEY_HOST` and `VALKEY_PORT` environment variables pointing to a Redis-compatible server.

## Configuration

### LucidLink API

1. Obtain an API token from your LucidLink admin console
2. Enter the API endpoint (e.g., `https://api.lucidlink.com/api/v1`)
3. Paste your Bearer token
4. Click "Load Filespaces"

### Creating a DataStore

1. Select a filespace from the dropdown
2. Click "+ Create new" in the DataStores card
3. Fill in the required fields:
   - **Name** - Unique identifier for the DataStore
   - **Bucket** - S3 bucket name
   - **Region** - AWS region (required)
   - **Access Key / Secret Key** - AWS credentials with bucket access
4. Click "Create DataStore"

### Browsing and Importing

1. Go to the **Browser** tab
2. If prompted, enter AWS credentials for the DataStore
3. Navigate folders and click:
   - **Import** - Import a single file
   - **+ Queue** - Queue a folder for bulk import
4. Monitor progress in the **Jobs** tab

### AWS SQS Event Stream (Automatic Imports)

Automatically import new files when they're uploaded to your S3 bucket.

> **Note:** This feature only works with AWS S3 buckets (not S3-compatible storage).

1. Go to the **AWS SQS** tab
2. Enter IAM credentials with SQS and S3 permissions
3. Click **Add queue** → **Create new queue**
4. Select the target DataStore and click **Create queue**

The app will:
- Create an SQS queue in AWS
- Configure the queue policy for S3 notifications
- Set up S3 bucket event notifications automatically

New files uploaded to the S3 bucket will be automatically imported to LucidLink.

**Required IAM Permissions:**
- `sqs:CreateQueue`, `sqs:GetQueueAttributes`, `sqs:SetQueueAttributes`
- `sqs:ReceiveMessage`, `sqs:DeleteMessage`
- `s3:GetBucketNotification`, `s3:PutBucketNotification`

## Project Structure

```
lucidlink-connect-web-ui/
├── app/
│   ├── main.py                 # FastAPI application
│   ├── services/
│   │   ├── lucidlink.py        # LucidLink API client
│   │   ├── s3_service.py       # S3 operations
│   │   ├── sqs_service.py      # SQS + S3 notification setup
│   │   ├── sqs_poller.py       # SQS polling cron job
│   │   ├── worker.py           # ARQ job worker
│   │   ├── state.py            # Application state
│   │   ├── database.py         # SQLite persistence
│   │   └── secrets.py          # Credential storage
│   ├── templates/
│   │   ├── base.html           # Layout template
│   │   └── partials/           # HTMX partial templates
│   └── static/
│       ├── css/style.css       # LucidLink brand CSS
│       ├── fonts/              # Aeonik font files
│       └── img/icons/          # SVG icons
├── docker-compose.yml
├── docker-compose.aws.yml       # AWS overlay (EFS + lucidlink-api)
├── Dockerfile
├── deploy.sh                    # AWS deployment CLI
├── terraform/
│   ├── main.tf                  # Provider, data sources
│   ├── vpc.tf                   # VPC, subnets, security groups
│   ├── alb.tf                   # ALB, ACM cert, listeners
│   ├── compute.tf               # IAM, launch template, ASG
│   ├── storage.tf               # EFS, S3 deploy bucket
│   ├── dns.tf                   # Route 53 record
│   ├── variables.tf
│   ├── outputs.tf
│   └── scripts/user-data.sh     # EC2 bootstrap
└── README.md
```

## API Documentation

Swagger UI is available at your API endpoint + `/docs`:
- Example: `https://api.lucidlink.com/api/v1/docs`

## Tech Stack

- **Backend**: FastAPI (Python 3.12)
- **Frontend**: HTMX + Alpine.js
- **Job Queue**: ARQ + Valkey
- **Styling**: Custom CSS (LucidLink brand guidelines)
- **Icons**: Lucide (MIT licensed)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LL_API_HOST` | `https://admin-api.solutions-eng.online/api/v1` | LucidLink API endpoint |
| `VALKEY_HOST` | `localhost` | Valkey/Redis host |
| `VALKEY_PORT` | `6379` | Valkey/Redis port |
| `DATA_DIR` | `/data` | Persistent data directory |
| `ARQ_MAX_JOBS` | `4` | Max concurrent jobs per worker |

**Production Variables (multi-user branch):**

| Variable | Required | Description |
|----------|----------|-------------|
| `DOMAIN` | Yes | Your domain name (e.g., `connect.example.com`) |
| `JWT_SECRET_KEY` | Yes | 64-char hex string: `openssl rand -hex 32` |
| `ADMIN_EMAIL` | No | Initial admin email (default: `admin@localhost`) |
| `ADMIN_PASSWORD` | No | Initial admin password (default: `admin`) |
| `CF_API_TOKEN` | No | Cloudflare API token for DNS-01 ACME challenges |

## Security

### Credential Storage

All sensitive credentials (AWS access keys, API tokens) are encrypted at rest:

- **Algorithm**: Fernet (AES-128-CBC + HMAC-SHA256)
- **Key Derivation**: PBKDF2 with 100,000 iterations from `JWT_SECRET_KEY`
- **Storage**: `$DATA_DIR/secrets.enc` with 0600 permissions

In local development mode (without `DATA_DIR`), credentials use the system keyring (macOS Keychain, Windows Credential Locker, or Linux Secret Service).

### Authentication

- JWT tokens stored in httponly cookies (not accessible to JavaScript)
- Passwords hashed with bcrypt
- Per-user data isolation (users cannot see each other's credentials)

## Production Deployment (multi-user branch)

1. **Configure DNS** - Point your domain to the server

2. **Create `.env` file:**
```bash
cp .env.example .env
```

3. **Edit `.env` with your settings:**
```bash
DOMAIN=connect.example.com
JWT_SECRET_KEY=your-64-char-hex-secret
ADMIN_EMAIL=admin@yourcompany.com
ADMIN_PASSWORD=secure-password-here
```

4. **Start production:**
```bash
./run.sh prod
```

Caddy automatically provisions Let's Encrypt certificates on first request.

## AWS Deployment (aws-deploy branch)

Deploys to a single EC2 instance behind an ALB with auto-recovery, EFS for persistent data, and a local `lucidlink/lucidlink-api` container. Estimated cost: ~$37/month.

**Prerequisites:** AWS CLI, Terraform, a Route 53 hosted zone for your domain.

```bash
./deploy.sh setup      # Configure secrets + Terraform variables (interactive)
./deploy.sh plan       # Preview infrastructure changes
./deploy.sh deploy     # Deploy infrastructure + upload application
```

**Architecture:**
- **ALB** - HTTPS termination (ACM cert, TLS 1.3), HTTP→HTTPS redirect, 5-min idle timeout for SSE
- **ASG (1/1/1)** - Auto-replaces failed instances, ELB health checks
- **EFS** - Persistent storage for SQLite DB, encrypted secrets, Valkey data, LucidLink API data
- **S3** - Application artifact storage for bootstrap and updates
- **SSM** - Secrets (SecureString) and instance access (no SSH keys required)
- **LucidLink API** - `lucidlink/lucidlink-api` container in Docker network (`http://lucidlink-api:3003/api/v1`)

**Management:**

| Command | Description |
|---------|-------------|
| `./deploy.sh status` | Show instance health and ALB target status |
| `./deploy.sh ssh` | Connect to instance via SSM (no SSH keys) |
| `./deploy.sh logs` | View application logs (`app` or `boot`) |
| `./deploy.sh update` | Push code changes to running instance |
| `./deploy.sh secrets` | List or rotate SSM secrets |
| `./deploy.sh destroy` | Tear down all infrastructure |

