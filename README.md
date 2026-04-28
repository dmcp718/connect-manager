# LucidLink Labs: CONNECT Manager — `aws-fargate`

A web application for importing S3 objects into LucidLink filespaces using the External Data Store API. This branch deploys CONNECT Manager on **AWS ECS Fargate** with RDS Postgres, ElastiCache Valkey, an ALB, and ARQ workers that scale on queue depth.

## Branches

| Branch | Description | Cost |
|---|---|---|
| `main` | Multi-user with JWT auth, dev-friendly Compose | local dev |
| `multi-user` | `main` + Caddy reverse proxy with HTTPS | self-hosted |
| `aws-deploy` | EC2 + ASG + ALB + EFS, Compose-on-EC2 | ~$37/mo |
| **`aws-fargate` (this branch)** | **ECS Fargate + RDS + ElastiCache + ALB** | **~$80–110/mo** |

## Architecture

- **ALB** — HTTPS termination (ACM), HTTP→HTTPS redirect, 5-min idle timeout for SSE.
- **ECS Fargate web service** — FastAPI + the `lucidlink-api` sidecar in the same task. Public via ALB target group, runs on `FARGATE`.
- **ECS Fargate worker service** — ARQ worker, runs on `FARGATE_SPOT`, autoscales 0→N on a custom CloudWatch queue-depth metric.
- **ECS one-shot migrate task** — Alembic runs via `aws ecs run-task` on deploy. Workers/web do not migrate at startup (multi-replica race).
- **RDS Postgres 16** — single AZ (Multi-AZ optional), encrypted at rest with the project KMS CMK.
- **ElastiCache Valkey 8** — TLS in transit + AUTH token, encrypted at rest with the same KMS CMK.
- **Secrets Manager** — JWT key, admin bootstrap, RDS master, Valkey AUTH. Paths follow `/connect/<env>/<purpose>`.
- **CloudWatch alarms** — task health, ALB 5xx, queue depth, RDS CPU.
- **GitHub OIDC role** — passwordless deploy from GitHub Actions.

See `SPEC.md` for ADRs and the full decision log.

## Deploying

### Prerequisites

- AWS account with admin (or equivalent) IAM permissions
- Route 53 hosted zone for your domain
- AWS CLI v2, Terraform ≥ 1.5, `jq`, `gh`
- Python 3.12 + [uv](https://docs.astral.sh/uv/)

### Bootstrap TUI (recommended)

The fastest path is the Textual-based bootstrap wizard, which walks through the entire deployment.

```bash
cd bootstrap
pip install -e .
connect-bootstrap
```

The wizard handles: dependency checks → AWS auth → `terraform.tfvars` → plan/apply → Secrets Manager seeding → ECS cluster verification → CONNECT install → status dashboard. See `bootstrap/README.md` for details.

### Manual Terraform

If you'd rather drive Terraform yourself:

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # then edit
terraform init                                  # populate backend.tf first
terraform apply
```

After apply, push the GitHub OIDC role ARN into the repo's `AWS_ROLE_ARN` secret so CI can deploy. Subsequent deploys are handled by `.github/workflows/aws-fargate.yml`.

### Local development (no AWS)

The `local/` ministack emulates ~30 AWS services on `localhost:4566`, plus plain Postgres + Valkey containers for the data plane.

```bash
bash local/bootstrap.sh
docker compose -f local/docker-compose.local.yml up -d
cd app
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Run tests with:

```bash
cd app
uv run pytest -m 'not real_aws'
```

See `CLAUDE.md` for the full local-first dev loop.

## Configuration

The web/worker tasks receive their configuration via Terraform. The most relevant variables (in `terraform/variables.tf`):

| Variable | Default | Description |
|---|---|---|
| `env` | `prod` | Environment name (used in resource names and secret paths) |
| `aws_region` | `us-east-1` | AWS region |
| `domain_name` | required | Public hostname (e.g., `connect.example.com`) |
| `route53_zone_id` | required | Route 53 zone ID for the domain |
| `web_image_tag`, `worker_image_tag` | required | ECR image tags to deploy |
| `web_desired_count` | `2` | Web service task count |
| `worker_min_capacity` | `0` | Worker autoscale floor (0 = scale to zero) |
| `worker_max_capacity` | `4` | Worker autoscale ceiling |
| `log_level` | `INFO` | Application log level (also overridable via `LOG_LEVEL` env) |

App-level env vars (set on the task definitions, not in `.env`):

| Variable | Source | Description |
|---|---|---|
| `DATABASE_URL` | Secrets Manager `/connect/<env>/db` | asyncpg URL for RDS |
| `VALKEY_URL` | Secrets Manager `/connect/<env>/valkey` | `rediss://` URL with AUTH token |
| `JWT_SECRET_KEY` | Secrets Manager `/connect/<env>/jwt` | 64-char hex; derives Fernet key |
| `ENV`, `AWS_REGION`, `LOG_LEVEL` | Terraform `app_env_vars` | Plaintext env |
| `LL_API_HOST` | Task definition | LucidLink API hostname (sidecar) |

## Secret naming

Per `CLAUDE.md` "Naming":

| Secret | Path | Owner |
|---|---|---|
| JWT key | `/connect/<env>/jwt` | `terraform/modules/secrets` |
| Admin bootstrap | `/connect/<env>/admin` | `terraform/modules/secrets` |
| RDS master | `/connect/<env>/db` | `terraform/modules/rds` |
| Valkey AUTH | `/connect/<env>/valkey` | `terraform/modules/elasticache` |

All four are encrypted with the project KMS CMK (`alias/connect-secrets`).

## Customer credential boundary

Customer-supplied AWS keys (for browsing their S3 buckets and consuming SQS) are Fernet-encrypted at rest in Postgres. The Fernet key is derived from `JWT_SECRET_KEY` via PBKDF2 (100k iterations).

App-owned AWS calls (writing CloudWatch metrics, reading Secrets Manager, etc.) use the **ECS Task Role only** — no static IAM keys for app-owned actions ever live in container env or in Secrets Manager values.

## Testing

```bash
cd app
uv run pytest -m 'not real_aws'                    # default: local stack
uv run pytest -m 'real_aws'                        # gated; needs real AWS
uv run pytest tests/security tests/load            # security + load profiles
```

Snapshot tests for the bootstrap TUI live in `bootstrap/tests/`.

## Repository layout

```
lucidlink-connect-web-app/
├── app/                       # FastAPI + ARQ worker + tests
│   ├── main.py
│   ├── services/              # auth, aws, database, lucidlink, sqs, worker, …
│   ├── db/                    # SQLAlchemy models + repositories
│   ├── templates/             # Jinja2 partials (HTMX)
│   └── static/                # CSS/JS/images
├── alembic/                   # Postgres migrations
├── bootstrap/                 # operator-facing Textual TUI (Epic 6)
├── terraform/
│   ├── main.tf                # root composition
│   ├── variables.tf, outputs.tf
│   └── modules/               # vpc, ecs-cluster, alb, task-iam,
│                              #   ecs-service-{web,worker}, ecs-task-migrate,
│                              #   queue-metric, rds, elasticache, secrets,
│                              #   acm-route53, ecr, alarms, github-oidc
├── local/                     # ministack-based dev stack
├── docs/                      # operator runbooks
├── .github/workflows/         # CD: aws-fargate.yml
├── CLAUDE.md                  # branch constitution (rules every session reads)
├── SPEC.md                    # architecture + ADRs
├── SWARM.md                   # build phases
└── README.md
```

## Tech stack

- **Backend:** FastAPI (Python 3.12), SQLAlchemy + asyncpg, ARQ, httpx
- **Frontend:** HTMX + Alpine.js (unchanged from `main`)
- **Job queue:** ARQ on Valkey 8
- **Infra:** Terraform 1.5+, AWS provider 5.26+
- **Bootstrap UI:** Textual (TUI)
- **CI/CD:** GitHub Actions with OIDC

## Security

- All persistent secrets in AWS Secrets Manager, encrypted with a customer-managed KMS CMK.
- Customer S3/SQS credentials Fernet-encrypted in Postgres.
- TLS 1.3 at the ALB; TLS in transit to ElastiCache.
- ECS Task Role enforces least-privilege for app-owned AWS calls.
- ALB security group allows only 443 from the public; web tasks accept only ALB-sourced traffic.

For a full security audit, see `tests/security/audit-checklist.md`.
