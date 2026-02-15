# AWS Deployment Plan: LucidLink Connect Manager

## Architecture Analysis

### Current State
| Component | Technology | Constraint |
|-----------|-----------|------------|
| Web | FastAPI + Uvicorn (:8000) | Stateless (auth via JWT cookie) |
| Workers | ARQ (async Redis queue), 2-4 replicas | Must share `/data` volume with web |
| LucidLink API | `lucidlink/lucidlink-api` (:3003) | Local container via Docker network |
| Database | **SQLite** at `/data/state.db` | File-based -- all services must share filesystem |
| Queue/Cache | Valkey (Redis-compatible) (:6379) | AOF persistence, pub/sub for log streaming |
| Secrets | Fernet-encrypted file at `/data/secrets.enc` | File-based, key derived from `JWT_SECRET_KEY` |
| TLS | ALB (HTTPS termination) | ACM certificate, TLS 1.3 |
| Health | `GET /health` (liveness only) | No deep DB/Valkey connectivity check |

### Critical Constraint: SQLite
SQLite requires all readers/writers on the **same filesystem**. This means web + all workers must run on a single host (or share an EFS volume, which adds latency). This is the primary factor limiting horizontal scaling and true multi-node HA.

### LucidLink API (local container)
The `lucidlink/lucidlink-api` container runs in the Docker Compose stack alongside web/worker/valkey. Communication happens over the Docker network at `http://lucidlink-api:3003/api/v1` -- no external API endpoint needed. The `LL_API_HOST` env var is set automatically by `docker-compose.aws.yml`, and the Settings UI pre-fills this default so users only need to provide their API token.

### External Dependencies (outbound)
- AWS S3 (boto3, for object browsing and import)
- AWS SQS (boto3, polled every 10s for event-driven imports)
- CDN: unpkg.com (HTMX, Alpine.js), fonts.googleapis.com (Inter)

---

## Deployment Options

### Option A: EC2 + ASG Auto-Recovery (Recommended)
**Best balance of HA and cost for the current architecture.**

```
                    ┌─────────────┐
                    │   Route 53  │
                    │  DNS CNAME  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │     ALB     │
                    │  HTTPS:443  │
                    │  (TLS term) │
                    └──────┬──────┘
                           │ HTTP:8000
                    ┌──────▼──────┐
                    │  ASG (1/1/1)│
                    │   t3.small  │
                    │             │
                    │ ┌──────────────┐ │
                    │ │ web x1       │ │
                    │ │ worker x4    │ │
                    │ │ valkey x1    │ │
                    │ │ ll-api x1    │ │
                    │ └──────────────┘ │
                    │  Docker     │
                    │  Compose    │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  EBS gp3    │
                    │  20 GB      │
                    │  /data vol  │
                    └─────────────┘
```

**How it works:**
- Single EC2 instance runs all services via Docker Compose (`prod-shared` mode)
- ALB handles HTTPS termination (no Caddy needed), forwards HTTP to :8000
- ASG (min=1, max=1, desired=1) auto-replaces unhealthy instances
- ALB health checks hit `GET /health` every 30s
- EBS volume persists SQLite DB + encrypted secrets across instance replacements
- EBS snapshots via AWS Backup for disaster recovery

**HA characteristics:**
- Instance failure: ASG launches replacement in ~3-5 minutes
- AZ failure: ASG can launch in another AZ (EBS snapshot restore needed)
- Data durability: EBS 99.999% + automated snapshots
- **Downtime during recovery: 3-5 min (acceptable for internal tool)**

**Cost estimate (us-east-1):**
| Resource | Spec | Monthly Cost |
|----------|------|-------------|
| EC2 t3.small | 2 vCPU, 2 GB | ~$15 (on-demand) or ~$9 (reserved 1yr) |
| ALB | Basic traffic | ~$18 |
| EBS gp3 | 20 GB | ~$2 |
| AWS Backup | Daily snapshots, 7-day retention | ~$1 |
| Route 53 | Hosted zone + queries | ~$1 |
| **Total** | | **~$37/mo** (on-demand) or **~$31/mo** (reserved) |

**Pros:**
- No code changes needed -- deploy as-is with `docker-compose.prod-shared.yml`
- Cheapest option that provides automated recovery
- Simple to operate and debug (SSH into instance, `docker compose logs`)
- EBS snapshots provide point-in-time recovery

**Cons:**
- 3-5 minute downtime during instance replacement
- Single AZ by default (cross-AZ recovery requires EBS snapshot restore = longer downtime)
- No horizontal scaling (SQLite constraint)

---

### Option B: ECS Fargate + EFS (No Code Changes)
**Serverless containers with shared filesystem for SQLite.**

```
                    ┌─────────────┐
                    │   Route 53  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │     ALB     │
                    │  HTTPS:443  │
                    │ sticky sess │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────▼─────┐     │     ┌──────▼─────┐
        │  web task  │     │     │worker task │
        │  (Fargate) │     │     │x4 (Fargate)│
        └─────┬──────┘     │     └──────┬─────┘
              │            │            │
              │     ┌──────▼──────┐     │
              │     │   Valkey    │     │
              │     │ ElastiCache │     │
              │     │  (t3.micro) │     │
              │     └─────────────┘     │
              │                         │
              └────────┬────────────────┘
                       │
                ┌──────▼──────┐
                │    EFS      │
                │  /data vol  │
                │ (Multi-AZ)  │
                └─────────────┘
```

**How it works:**
- ECS Fargate runs web and worker as separate services (no EC2 to manage)
- EFS (Elastic File System) provides shared `/data` mount for SQLite + secrets
- ElastiCache replaces the Valkey container (managed Redis-compatible service)
- ALB with sticky sessions for SSE (Server-Sent Events) stability

**HA characteristics:**
- Task failure: ECS auto-restarts in ~30-60 seconds
- AZ failure: EFS is multi-AZ, ECS can launch tasks in other AZs
- Better availability than Option A

**Cost estimate:**
| Resource | Spec | Monthly Cost |
|----------|------|-------------|
| Fargate (web) | 0.5 vCPU, 1 GB, always-on | ~$15 |
| Fargate (workers x4) | 0.25 vCPU, 0.5 GB each | ~$15 |
| ElastiCache | t3.micro (single node) | ~$12 |
| EFS | Infrequent Access, ~1 GB | ~$3 |
| ALB | Basic traffic | ~$18 |
| Route 53 | | ~$1 |
| **Total** | | **~$64/mo** |

**Pros:**
- No EC2 instances to manage (patching, SSH keys, etc.)
- Faster recovery (~30-60s vs 3-5 min)
- Multi-AZ by default (EFS + Fargate)
- No code changes (SQLite still works over EFS)

**Cons:**
- EFS adds ~1-5ms latency to every SQLite operation (noticeable but acceptable for this workload)
- ElastiCache is a fixed cost even at low usage
- More complex setup (ECS task definitions, service discovery, IAM roles)
- Harder to debug than SSH into an EC2

---

### Option C: ECS Fargate + RDS PostgreSQL (Full HA)
**Production-grade HA requiring a database migration.**

```
                    ┌─────────────┐
                    │   Route 53  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │     ALB     │
                    │  HTTPS:443  │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────▼─────┐     │     ┌──────▼─────┐
        │  web task  │     │     │worker task │
        │  x2 (HA)  │     │     │x4          │
        └─────┬──────┘     │     └──────┬─────┘
              │            │            │
              │     ┌──────▼──────┐     │
              │     │ ElastiCache │     │
              │     │  (Valkey)   │     │
              │     └─────────────┘     │
              │                         │
              └────────┬────────────────┘
                       │
                ┌──────▼──────┐
                │ RDS Postgres│
                │  Multi-AZ   │
                │ db.t4g.micro│
                └─────────────┘
```

**Requires:**
- Migrate SQLite to PostgreSQL (database.py rewrite with asyncpg or psycopg)
- Migrate Fernet secrets file to AWS Secrets Manager or DB-stored secrets
- Update connection logic throughout the codebase

**Cost estimate:**
| Resource | Spec | Monthly Cost |
|----------|------|-------------|
| Fargate (web x2) | 0.5 vCPU, 1 GB each | ~$30 |
| Fargate (workers x4) | 0.25 vCPU, 0.5 GB each | ~$15 |
| ElastiCache | t3.micro (single node) | ~$12 |
| RDS PostgreSQL | db.t4g.micro, Multi-AZ | ~$28 |
| ALB | | ~$18 |
| Route 53 | | ~$1 |
| Secrets Manager | ~10 secrets | ~$4 |
| **Total** | | **~$108/mo** |

**Pros:**
- True multi-AZ HA with zero single points of failure
- Horizontal scaling (add more web/worker tasks freely)
- Managed database backups, patching, failover
- Production-grade for larger user bases

**Cons:**
- Requires significant code changes (SQLite → PostgreSQL migration)
- 3x the cost of Option A
- More operational complexity (RDS, ElastiCache, ECS all to manage)

---

## Recommendation

### Start with Option A, plan for Option B

**Phase 1 (Now): Deploy Option A**
- Zero code changes, deploy this week
- $31-37/month, automated instance recovery
- Perfectly adequate for an internal tool with <10 concurrent users
- 3-5 min recovery time is acceptable for non-critical workloads

**Phase 2 (If needed): Migrate to Option B**
- Trigger: Need for faster recovery or multi-AZ resilience
- Still no code changes (SQLite over EFS works)
- ~$64/month, ~30-60s recovery

**Phase 3 (If significantly scaling): Migrate to Option C**
- Trigger: >20 concurrent users, SQLite becomes a bottleneck, or strict HA SLA required
- Requires PostgreSQL migration (significant dev effort)
- ~$108/month, true zero-downtime HA

---

## Option A Implementation Details

### AWS Resources Required

```
VPC (existing or new)
├── Public Subnets (2 AZs minimum for ALB)
│   └── ALB (internet-facing)
├── Private Subnet
│   └── EC2 (t3.small) in ASG
│       ├── EBS gp3 20GB (/data)
│       └── Docker Compose (web + worker + valkey)
├── Security Groups
│   ├── ALB SG: inbound 443 from 0.0.0.0/0
│   └── EC2 SG: inbound 8000 from ALB SG only
└── IAM
    └── EC2 Instance Role
        ├── SSM access (for management, no SSH needed)
        └── EBS snapshot permissions
```

### Infrastructure (Terraform)

```
terraform/
├── main.tf              # Provider, backend (S3 + DynamoDB)
├── vpc.tf               # VPC, subnets, IGW, NAT (or use existing)
├── alb.tf               # ALB, target group, HTTPS listener, ACM cert
├── asg.tf               # Launch template, ASG (1/1/1), user data
├── ec2.tf               # Security groups, IAM instance role
├── ebs.tf               # EBS volume, backup plan
├── dns.tf               # Route 53 record → ALB
├── variables.tf         # Domain, instance type, etc.
├── outputs.tf           # ALB DNS, instance ID
└── scripts/
    └── user-data.sh     # Install Docker, clone repo, start services
```

### User Data Script (Bootstrap)

The EC2 user data script would:
1. Install Docker and Docker Compose
2. Clone the repo (or pull container images from ECR)
3. Attach/mount the EBS data volume at `/data`
4. Create `.env` from SSM Parameter Store or Secrets Manager
5. Run `./run.sh prod-shared` (or equivalent docker compose up)
6. Configure the ALB target group health check

### Secrets Management

| Secret | Storage | How Accessed |
|--------|---------|-------------|
| `JWT_SECRET_KEY` | SSM Parameter Store (SecureString) | User data script → `.env` |
| `ADMIN_EMAIL` | SSM Parameter Store | User data script → `.env` |
| `ADMIN_PASSWORD` | SSM Parameter Store (SecureString) | User data script → `.env` |
| `DOMAIN` | SSM Parameter Store | User data script → `.env` |
| `LL_API_HOST` | Docker Compose (hardcoded) | `http://lucidlink-api:3003/api/v1` via Docker network |

### Deployment Workflow

```
Developer pushes to multi-user branch
        │
        ▼
SSH/SSM into EC2 instance
        │
        ▼
git pull && ./run.sh prod-shared-build
        │
        ▼
ALB health check confirms /health returns 200
```

Future improvement: GitHub Actions or CodeDeploy for automated deployments.

### Monitoring

- **ALB**: Target health, request count, latency, 5xx errors (CloudWatch)
- **EC2**: CPU, memory (CloudWatch agent), disk usage
- **ASG**: Instance health, scaling events
- **EBS**: Volume throughput, IOPS
- **Alarms**: Unhealthy target (SNS → email), high CPU >80% sustained

---

## Decision Matrix

| Criteria | A: EC2+ASG | B: Fargate+EFS | C: Fargate+RDS |
|----------|:----------:|:--------------:|:--------------:|
| Monthly cost | **$31-37** | $64 | $108 |
| Code changes | **None** | **None** | Major |
| Recovery time | 3-5 min | **30-60s** | **30-60s** |
| Multi-AZ | Partial | **Yes** | **Yes** |
| Horizontal scaling | No | No | **Yes** |
| Operational complexity | **Low** | Medium | High |
| Setup time | **1-2 days** | 3-5 days | 2-3 weeks |
| Debug ease | **High (SSH)** | Medium | Medium |

**Recommendation: Option A** -- delivers automated recovery at minimal cost with zero code changes. Upgrade path to B or C exists when requirements justify it.
