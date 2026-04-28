# CLAUDE.md — CONNECT Manager `aws-fargate` branch

> **Branch posture:** `aws-fargate` runs in **parallel** with `main` and `aws-deploy`. Frontend (`app/templates/**`, `app/static/**`) and the public HTTP API surface are **unchanged from `main`**. All work on this branch is backend deployment + targeted app refactor for Fargate readiness.
>
> **Read this file on every session start.** It is the constitution. For architecture and decisions, see `SPEC.md`. For build phases, see `SWARM.md`.

---

## Project overview

CONNECT Manager is a FastAPI + ARQ + Valkey web app that imports S3 objects into LucidLink filespaces via the LucidLink External Data Store HTTP API. The `aws-fargate` branch ships a production-grade ECS Fargate deployment alongside the existing EC2-based `aws-deploy`: ALB + Fargate web tasks (with the lucidlink-api as a sidecar), Fargate worker tasks with custom-metric autoscaling on ARQ queue depth, RDS Postgres, ElastiCache Valkey, Secrets Manager + KMS CMK, ECR, CloudWatch alarms, GitHub Actions CD, and the application-side refactor required to run cleanly on Fargate (SQLite→Postgres, /health + /ready probes, structured JSON logging, idempotent worker, graceful SIGTERM drain, Task-Role-aware boto3).

Full architecture and decisions: `SPEC.md`. Build phases: `SWARM.md`.

---

## Beads — MANDATORY workflow

Beads (`bd`) is the project's execution backbone. Every session follows this protocol.

### Session start

1. Run `bd ready`. This is your work queue.
2. Claim your task: `bd update awsk-XXXX --claim`. Never start work without claiming first.

### During work

3. If you discover a bug or issue **unrelated** to your current task, file it immediately:
   ```bash
   bd create "Bug: <description>" -p 2 -t bug
   ```
4. If a task turns out to be bigger than expected, split it: `bd create "<subtask>" --parent awsk-XXXX`.
5. **3-strike rule:** if you fail the same task 3 times, do not keep retrying. Run:
   ```bash
   bd update awsk-XXXX --status blocked
   bd create "Blocker: <what failed and what was tried>" -p 1 -t bug --deps blocks:awsk-XXXX
   ```
   Then move on.

### After each completed task

6. Mark closed: `bd update awsk-XXXX --status closed`.
7. **Commit immediately** with a `Closes awsk-XXXX` line in the commit body.

### Recovery (crashed or interrupted session)

8. On session start, run `bd list --status in_progress`. If you see tasks claimed by your name that you're not working on, reset them.

### CLI gotchas

- `bd sync` and `--unclaim` do not exist in this Beads build.
- `bd create` uses `-a` for assignee (not `--assign`).
- Binary at `/home/mini-admin/go/bin/bd`.

---

## Local-first dev workflow

**Iterate against the local stack before any real-AWS interaction.** Real AWS is reserved for the F2 / Epic 8 real-AWS smoke task, hard-capped at $50.

### Inner loop

```bash
bash local/bootstrap.sh                    # idempotent
docker compose -f local/docker-compose.local.yml up -d
pytest app/tests/ -m 'not real_aws'
```

### What the local stack provides

- **`ministack` container on port 4566** — emulates ~30 AWS services. Set `AWS_ENDPOINT_URL=http://localhost:4566`.
- **Plain `postgres:16` and `valkey:8` containers** — provide the data plane (NOT the ministack-allocated RDS/ElastiCache; simpler).
- **Static creds for Task Role emulation:** `AWS_ACCESS_KEY_ID=test`, `AWS_SECRET_ACCESS_KEY=test`. ministack accepts any non-empty values; boto3's default chain finds env creds and never reaches the (absent) ECS container credentials endpoint.

### What the local stack does NOT validate

These get verified only in the real-AWS smoke task:

- ECS Task Role → boto3 cred resolution via `$AWS_CONTAINER_CREDENTIALS_FULL_URI`
- ALB target group health checks during rolling updates
- ACM cert provisioning + DNS-01 validation
- ElastiCache TLS + AUTH (local Valkey runs plaintext)
- RDS automated backups, parameter groups, snapshot restore
- Cross-AZ failover
- Service Auto Scaling with the queue-metric Lambda
- CloudWatch alarm firing

If your task can only be validated by one of the above, tag it `real-aws`.

---

## Architecture rules

Non-negotiable. Violating them creates the merge-conflict and coupling failures the queue is designed to avoid.

1. **Frontend is unchanged.** This branch does not touch `app/templates/**` or `app/static/**`.
2. **Public HTTP API surface is unchanged.** Endpoints listed in `main`'s `CLAUDE.md` keep their paths, methods, and response shapes.
3. **Customer credential UX is unchanged.** Static IAM-user keys for customer S3/SQS access stay in the existing endpoints. AssumeRole / role-ARN flows are out of scope.
4. **App tasks get AWS creds via Task Role only.** No static AWS keys for app-owned actions in container env or Secrets Manager values. `app/services/aws.py` constructs boto3 clients with **no explicit credentials** for app-owned calls and lets the SDK find them via the ECS container credentials provider in prod or env locally.
5. **Customer credentials remain Fernet-encrypted at rest.** They live in Postgres, not SQLite. Fernet key derivation from `JWT_SECRET_KEY` is unchanged.
6. **Migrations run via one-shot `aws ecs run-task` on deploy.** Web/worker tasks do **not** run Alembic on startup (multi-replica + startup-migration is a race). Workflow gates `update-service` on the migrate task exiting cleanly.
7. **Workers must be idempotent.** Scale-to-zero + FARGATE_SPOT preemption can cause the same enqueued job to be picked up by a fresh worker. The `processed_jobs` table is the dedup primitive — check-and-skip on every job entry.
8. **No FUSE, no hostPath, no volume mounts for LucidLink.** The app talks to `lucidlink-api` over HTTP only (sidecar in the web task).
9. **Greenfield only.** No migration tooling from `aws-deploy`. An operator switching from Compose to Fargate starts fresh.

---

## File ownership

| Path | Owner |
|---|---|
| `terraform/modules/**` | infra-agent |
| `terraform/main.tf` | **Lead** (root composition) |
| `terraform/variables.tf`, `terraform/outputs.tf` | **Lead** |
| `app/db/**` | app-agent |
| `app/routes/health.py` | app-agent |
| `app/services/aws.py` | app-agent |
| `app/services/database.py` | app-agent |
| `app/services/*.py` (existing) | app-agent (Postgres-conversion edits only) |
| `app/main.py` | app-agent (Postgres + probe wiring only; new routes need Lead approval) |
| `app/templates/**`, `app/static/**` | **untouched** |
| `bootstrap/**` | app-agent (Epic 6 TUI) |
| `local/**` | infra-agent |
| `.github/workflows/**` | ci-agent proposes via PR; **Lead merges** |
| `pyproject.toml`, `requirements*.txt` | **Lead** |
| `Dockerfile` | ci-agent |
| `CLAUDE.md`, `SPEC.md`, `SWARM.md`, `README.md` | **Lead** |

---

## Conventions

### Python

- **Version:** Python 3.12.
- **Style:** `ruff check` + `ruff format` clean.
- **Types:** `mypy` strict on `app/`. New code is fully typed.
- **Async-first:** SQLAlchemy + asyncpg; `httpx` for outbound HTTP.
- **No comments except where the WHY is non-obvious** (hidden constraint, subtle invariant, workaround).
- **Logs in JSON.** Use `app/services/logging.py`. Required fields: `timestamp`, `level`, `logger`, `message`. Add `request_id`, `user_id`, `job_id` where applicable.

### Terraform

- **Style:** `terraform fmt -recursive` clean.
- **Module-per-concern:** `vpc`, `ecs-cluster`, `alb`, `task-iam`, `ecs-service-web`, `ecs-service-worker`, `ecs-task-migrate`, `queue-metric`, `rds`, `elasticache`, `secrets`, `acm-route53`, `ecr`, `alarms`, `github-oidc`. Each independently destroyable.
- **No hardcoded ARNs/IDs.** Module outputs feed module inputs via `terraform/main.tf`.
- **`terraform validate` clean** before commit.
- **Caller-owned SGs:** `rds`, `elasticache`, `ecs-service-*` modules expect SG IDs as inputs. Root composition defines them with cross-references.
- **State remote in S3** with DynamoDB lock (`terraform/backend.tf`, populated by operator).

### Git

- **Conventional commits:** `feat(infra):`, `feat(chart):` (deprecated on this branch), `feat(app):`, `fix(...):`, `docs(...):`, `test(...):`, `chore(...):`.
- **One bead = one commit minimum.** Lead-composition commits referencing multiple beads are an exception (call it out in the body).
- **Reference the bead:** `Closes awsk-XXXX` in the commit body.
- **No Co-Authored-By trailer** for Claude on this project.

### Naming

- **ECS resources:** `connect-<env>-<component>` (kebab-case)
- **Terraform resources:** `connect_<component>_<thing>` (snake_case)
- **Secrets Manager paths:** `/connect/<env>/<purpose>` (e.g., `/connect/prod/jwt`)
- **CloudWatch log groups:** `/aws/ecs/connect-<env>/<component>`, `/aws/lambda/connect-<env>-<fn>`
- **IAM roles:** `connect-<env>-<role-name>` (e.g., `connect-prod-task-execution`)
- **ECR repositories:** `connect-<component>` (e.g., `connect-web`)

---

## Testing

### Required for every closed task

- Unit tests for new logic.
- Integration tests for any DB- or external-API-touching path (testcontainers Postgres, ministack via `AWS_ENDPOINT_URL`).
- Terraform changes: `terraform fmt -recursive` + `terraform validate` clean.
- Local stack still produces a working state after the change.

### Test markers (pytest)

- Default: runs against local stack (ministack + Postgres + Valkey containers).
- `@pytest.mark.real_aws`: requires real AWS credentials. Excluded by default; only runs in the real-AWS smoke task.
- `@pytest.mark.slow`: takes >30s. Excluded from PR runs; included in pre-merge.

### What "done" means

A Beads task is closeable when:

- Code compiles cleanly (Python typecheck, Terraform validate).
- Tests pass for the changed component.
- No `# TODO` left without a corresponding bead.
- The change is committed with `Closes awsk-XXXX`.

---

## Branch posture (relationship to other branches)

| Branch | What it is | Status |
|---|---|---|
| `main` | Multi-user with JWT auth, dev-friendly Compose | Maintained |
| `multi-user` | `main` + Caddy HTTPS | Maintained |
| `aws-deploy` | EC2 + ASG + ALB + EFS, Compose-on-EC2, ~$37/mo | Maintained |
| **`aws-fargate` (this branch)** | **ECS Fargate + RDS + ElastiCache + ALB, ~$80–110/mo** | **Build target** |
| `aws-kubernetes` | EKS Auto Mode (abandoned during pivot to Fargate) | Local-only, not pushed |

This branch does not auto-merge to `main`. App-side changes that are useful on `main` (e.g., the `app/services/aws.py` helper, the `/health` + `/ready` split, structured logging) are cherry-picked back via separate PRs after this branch ships v1 — not before.

---

## Quick reference

### Beads

| Command | Purpose |
|---|---|
| `bd ready` | Actionable tasks |
| `bd update awsk-XXXX --claim` | Atomic claim |
| `bd create "<title>" -p <0-4>` | New task or bug |
| `bd update awsk-XXXX --status closed` | Mark done |
| `bd list --status in_progress` | Who's doing what |
| `bd list -t bug` | Discovered issues |

### Local stack

| Command | Purpose |
|---|---|
| `bash local/bootstrap.sh` | Bring up ministack + Postgres + Valkey |
| `docker compose -f local/docker-compose.local.yml up -d` | Restart aux services |
| `bash local/teardown.sh` | Tear down |
| `pytest app/tests/ -m 'not real_aws'` | Run the test suite |

### AWS (real, gated)

| Command | When |
|---|---|
| `cd terraform && terraform apply` | Real-AWS smoke only |
| `aws ecs update-service ...` | CD workflow runs this |
| `cd terraform && terraform destroy` | After smoke completes |

### CI host

GitHub Actions on **github.com/dmcp718/connect-manager**. Workflow at `.github/workflows/aws-fargate.yml`. Push branch + tag triggers documented there. The OIDC role is created by `module.github_oidc` and its ARN is the `AWS_ROLE_ARN` GitHub secret. Bitbucket origin remains the team source of truth.

---

*Read `SPEC.md` for architecture and `SWARM.md` for the build process. This file is the rules every session starts with — short, mandatory, non-negotiable.*
