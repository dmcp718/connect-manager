# SPEC.md — `aws-fargate` architecture and decisions

This document captures the architecture of the `aws-fargate` deployment and the decisions behind it. Read this when you need to understand *why* the stack looks the way it does. For day-to-day rules, read `CLAUDE.md`. For phase-by-phase build sequence, read `SWARM.md`.

---

## 1. Stack overview

```
                           ┌──────────────────┐
                           │   Route53        │
                           │   ALIAS A        │
                           │   var.domain     │
                           └────────┬─────────┘
                                    │
                           ┌────────▼─────────┐
                           │   ALB            │
                           │   HTTPS 443 →    │
                           │   target group   │
                           └────────┬─────────┘
                                    │
            ┌───────────────────────┴───────────────────────┐
            │                                               │
   ┌────────▼────────────┐                       ┌──────────▼──────────┐
   │  Web ECS Service    │                       │  Worker ECS Service │
   │  (FARGATE)          │                       │  (FARGATE_SPOT)     │
   │  desired=2, max=6   │                       │  min=0, max=8       │
   │  CPU autoscaling    │                       │  Queue-depth        │
   │                     │                       │  custom-metric      │
   │  ┌──────────────┐   │                       │  autoscaling        │
   │  │ web (FastAPI)│   │                       │                     │
   │  └──────┬───────┘   │                       │  ┌──────────────┐   │
   │         │ localhost │                       │  │worker (ARQ)  │   │
   │  ┌──────▼───────┐   │                       │  └─────┬────────┘   │
   │  │lucidlink-api │   │                       │        │            │
   │  │   :3003      │   │                       └────────│────────────┘
   │  └──────────────┘   │                                │
   └──────┬──────────────┘                                │
          │                                               │
          ├───────────────────┬───────────────────────────┤
          │                   │                           │
   ┌──────▼──────┐    ┌───────▼────────┐         ┌────────▼────────┐
   │  RDS        │    │  ElastiCache   │         │  Secrets Mgr    │
   │  Postgres16 │    │  Valkey 8      │◄────────┤  + CMK          │
   │  Multi-AZ?  │    │  TLS + AUTH    │         │                 │
   └─────────────┘    └────────────────┘         └─────────────────┘
                              ▲
                              │ LLEN every 1m
                       ┌──────┴───────┐
                       │ queue-metric │
                       │ Lambda       │
                       │ (in VPC)     │
                       └──────────────┘
                              │
                              ▼
                       CloudWatch metric:
                       Connect/ARQ.connect_arq_queue_depth
```

**Cost target (small-prod):** ~$80–110/month.

| Component | $/mo |
|---|---|
| ALB | ~$16 |
| RDS db.t4g.micro | ~$13 |
| ElastiCache cache.t4g.micro | ~$13 |
| NAT Gateway (1×) | ~$32 |
| Web Fargate (2× 0.75 vCPU / 1.5 GiB, 24×7) | ~$25 |
| Worker Fargate SPOT (avg 1× during bursts) | ~$3 |
| queue-metric Lambda (43k invocations/mo) | ~$0.20 |
| ECR storage + Secrets Manager + Logs | ~$10 |

vs. `aws-deploy` ~$37/mo and the abandoned EKS plan ~$175–230/mo.

---

## 2. Decisions

### ADR-001 — Pivot from EKS Auto Mode to ECS Fargate

**Status:** Decided 2026-04-27. Implemented in commit `5beac08` (salvage) and the F2 module work.

**Context:** The `aws-kubernetes` branch targeted EKS Auto Mode + Helm + KEDA + ESO. ~40 commits of work landed before the pivot.

**Why we pivoted:**

1. **Cost.** EKS fixed floor (control plane $73, NAT $32, RDS $13, ElastiCache $13, baseline node $15, logs/secrets/ECR ~$10) ≈ $170/mo before any worker activity. Worker scale-to-zero saves $20–40 of EC2 node spend; doesn't close the gap to `aws-deploy`. ECS Fargate has no control-plane fee and bills per task — comparable architectural wins at half the cost.

2. **Operational complexity.** k8s requires either dedicated platform staff or an opinionated managed posture. For a single-team product like Connect, Fargate gives the same managed-database + Pod-Identity-equivalent + ALB + image-based deploys at a much lower learning-curve floor.

3. **No structural use of k8s features.** We weren't using StatefulSets, DaemonSets, NetworkPolicies-as-policy, custom controllers, or any operator beyond ESO/KEDA — both of which have direct Fargate equivalents (Secrets Manager + Service Auto Scaling).

**What we kept (~70% of the work):**

- App refactor: SQLite→Postgres, /health + /ready, structured logging, idempotent worker, graceful drain.
- Terraform modules: vpc, rds, elasticache, secrets, ecr, acm-route53, alarms, github-oidc.
- Pytest fixtures, Alembic migrations, Dockerfile, `app/services/aws.py`.

**What we discarded:**

- Helm chart, EKS module, Pod-Identity-IAM module.
- NetworkPolicies (replaced by SG-based segmentation at root).
- KEDA ScaledObject (replaced by ECS Service Auto Scaling + a custom-metric Lambda).
- ESO ExternalSecrets (replaced by ECS Task Definition `secrets` blocks with `valueFrom`).

**What we added:**

- Six new modules: `task-iam`, `ecs-cluster`, `alb`, `ecs-service-web`, `ecs-service-worker`, `ecs-task-migrate`, `queue-metric`.
- Rewrote `github-oidc` to grant ECS deploy permissions instead of EKS.

### ADR-002 — Worker scale-to-zero requires an external metric publisher

**Status:** Decided. Implemented in `terraform/modules/queue-metric`.

**Context:** ECS Service Auto Scaling needs a CloudWatch metric to react to. ARQ queue depth lives in Valkey as a list (`LLEN arq:queue`).

**Options considered:**

| Option | Reject reason |
|---|---|
| Worker emits the metric | Breaks scale-to-zero — no worker → no metric → no scale-up. |
| Web emits the metric | Couples web to worker observability; multi-replica web means duplicate emissions. |
| **Standalone Lambda on EventBridge** | Selected. ~$0.20/mo at 1/min, runs even when both services are scaled down. |

**Implementation:** Lambda speaks just enough RESP (stdlib `socket` + `ssl`) to issue `AUTH` + `LLEN`. No `redis-py` dependency, fits in a single zip with no build step.

### ADR-003 — Web runs on FARGATE; worker on FARGATE_SPOT by default

**Status:** Decided.

**Context:** SPOT is ~70% cheaper but tasks can be terminated with 2-minute warning.

**Reasoning:**

- **Web:** mid-request termination is user-visible (broken connections, partial responses). Run on `FARGATE` (on-demand). The cost is small at 2 baseline replicas.
- **Worker:** mid-job termination is *safe* — ARQ + the `processed_jobs` dedup table guarantee idempotency on retry. Run on `FARGATE_SPOT`. SIGTERM → 120s `stopTimeout` lets ARQ complete in-flight work where possible.

`var.use_fargate_spot` on each service module is the override knob (default `true` for worker, `false` for web).

### ADR-004 — Migrations run as a one-shot `aws ecs run-task`, not at task startup

**Status:** Decided. Implemented in `terraform/modules/ecs-task-migrate` and `.github/workflows/aws-fargate.yml`.

**Context:** Multi-replica web tasks can't all run Alembic on startup — race conditions and lock contention. Need a single, ordered migration step before the rolling update.

**Implementation:**

1. CI builds + pushes new image.
2. CI registers a new revision of the `connect-<env>-migrate` task definition with the new image.
3. CI runs that task once (`aws ecs run-task` + `aws ecs wait tasks-stopped`).
4. CI checks the migrate container's exit code. Non-zero → fail the deploy.
5. Only on exit 0, CI registers new web/worker task def revisions and `update-service` with `--force-new-deployment`.

This replaces the Helm `pre-install`/`pre-upgrade` Job pattern from the abandoned EKS plan.

### ADR-005 — Caller-owned security groups

**Status:** Decided.

The `rds`, `elasticache`, and `ecs-service-*` modules accept `security_group_ids` as an input — they don't create their own. Root composition (`terraform/main.tf`) defines five SGs and threads them through:

- `web_tasks` — ingress from ALB SG only on 8000.
- `worker_tasks` — egress only.
- `rds` — ingress from web_tasks + worker_tasks on 5432.
- `valkey` — ingress from web_tasks + worker_tasks on 6379, plus a separate `aws_security_group_rule` from the queue-metric Lambda SG (avoids a circular dep).
- ALB SG lives inside `module.alb` (it's the only SG with a public CIDR ingress and is tightly bound to the ALB lifecycle).

This pattern keeps modules orchestrator-agnostic and avoids hidden coupling.

### ADR-006 — Customer-managed KMS via account-root key policy + IAM-side scoping

**Status:** Decided. Implemented in `terraform/modules/secrets` and `terraform/modules/task-iam`.

The connect-secrets CMK's key policy grants `kms:Decrypt` to the AWS account root. Fine-grained access is enforced by the Task Execution Role's IAM policy granting `kms:Decrypt` on the CMK ARN. This is the standard "IAM-delegating CMK" pattern and avoids the chicken-and-egg between the secrets module and task-iam.

### ADR-007 — github-oidc trust scope

**Status:** Decided.

The deploy role's trust policy accepts only:

- `repo:dmcp718/connect-manager:ref:refs/heads/aws-fargate`
- `repo:dmcp718/connect-manager:ref:refs/tags/v*`

Refs are `var.branch_refs` (default above) so reuse on a fork or a different branch is a single tfvars change. PRs (which run as `pull_request` events from forks) cannot assume the role — only direct pushes can.

The role's permissions are scoped tightly:

- ECR push limited to `connect-*` repos.
- ECS read-only is broad (most ECS Describe/List APIs don't accept resource constraints).
- ECS mutations (`UpdateService`, `RunTask`, `StopTask`) are scoped to the cluster ARN + sibling resource patterns.
- `iam:PassRole` is conditioned on `iam:PassedToService=ecs-tasks.amazonaws.com` and limited to the four task roles.

---

## 3. Customer credential boundary

**App-owned AWS calls** (reading own SQS queues, publishing own metrics, fetching own secrets) flow through `app/services/aws.py`, which constructs boto3 clients with no explicit credentials. boto3's default chain finds the ECS Task Role via `$AWS_CONTAINER_CREDENTIALS_FULL_URI`.

**Customer-owned AWS calls** (browsing customer S3 buckets, polling customer SQS queues for S3 notifications) use static IAM-user keys decrypted from Postgres and pass them explicitly via `boto3.client(..., aws_access_key_id=..., aws_secret_access_key=...)`. These flow through `app/services/s3_service.py` and `app/services/sqs_service.py`.

`app/services/aws.py` raises if explicit credentials are passed — it's a one-way gate to ensure the two code paths don't accidentally cross.

---

## 4. What's intentionally out of scope

- **AssumeRole / role-ARN flows** for customer credentials. Static IAM keys remain the contract. (Would change the `POST /api/datastores/{id}/credentials` shape — not safe to break.)
- **FUSE / hostPath / volume mounts for LucidLink.** The app talks to `lucidlink-api` over HTTP only. Filesystem-level integration is not part of this app.
- **Container-level NetworkPolicies.** SG-based segmentation at the AWS layer is the boundary.
- **Multi-region.** Single-region deployment. Multi-region is a future Epic.
- **Multi-tenancy controls** beyond per-user data isolation. Tenant separation is application-layer concern, not infra.
