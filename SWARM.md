# SWARM.md — `aws-fargate` build phases

This document tracks the build sequence for the `aws-fargate` branch. For architecture, see `SPEC.md`. For day-to-day rules, see `CLAUDE.md`.

---

## Phase status

| Phase | Status | Notes |
|---|---|---|
| Phase 1 — Branch + history surgery | ✓ Complete | Salvage commit `5beac08`, bead reorg `7ad9d04`, pushed to github + bitbucket. |
| Phase 2 — F2 ECS modules + composition | ✓ Complete | task-iam, ecs-cluster, alb, queue-metric, ecs-service-{web,worker}, ecs-task-migrate, root composition wired, terraform validate clean. |
| Phase 3 — App refactor (Epic 4) | Partial | Postgres data layer + /health + /ready + structured logging + idempotent worker + graceful drain landed in salvage. Remaining: convert `activity_logger.py` to Postgres (awsk-1l8), `await JobQueue.*` callers (awsk-7zy), SQLite utility queries (awsk-opc), flaky drain test fix (awsk-ap6). |
| Phase 4 — CI workflow + secrets push (Epic 5) | Partial | Workflow `.github/workflows/aws-fargate.yml` landed. Operator must `gh secret set AWS_ROLE_ARN` (awsk-7yj) + create `staging` and `production` GitHub environments. |
| Phase 5 — Bootstrap TUI (Epic 6) | Not started | 12 sub-tasks under awsk-8b6. Refactored from EKS-specific to ECS-specific. |
| Phase 6 — Cross-cutting docs (Epic 7) | Partial | CLAUDE.md, SPEC.md, SWARM.md rewritten in F2.10. Operator runbook, architecture diagram, cost spreadsheet, DR runbook still pending. |
| Phase 7 — Polish + QA (Epic 8) | Not started | 6 sub-tasks under awsk-3r4. Includes the real-AWS smoke run ($50 hard cap). |

---

## Module build order (for reference)

The dependency graph for the F2 modules is:

```
vpc ─────┐
         ├──► ecs_cluster ──► alb ──┐
         │                          │
secrets ─┤                          ├──► task_iam ──┐
         │                          │               │
         ├──► rds ──────────────────┤               │
         │                          │               │
         ├──► elasticache ──► queue_metric          │
         │                          │               │
ecr  ────┤                          │               │
         │                          ▼               │
         └──► acm_route53 ──┐  ecs_service_web ◄────┤
                            │                       │
                            └──► ecs_service_worker ┘
                                       │
                                       ▼
                                    alarms
                                       │
                                       ▼
                                   github_oidc
```

---

## Operator hand-off (Phase 7 minimum)

For the real-AWS smoke run, the operator needs to:

1. Create the Route53 hosted zone for `var.domain` and delegate it.
2. Create an S3 bucket + DynamoDB table for Terraform state, populate `terraform/backend.tf`.
3. `terraform init && terraform apply` (~25 min on cold create).
4. `gh secret set AWS_ROLE_ARN -R dmcp718/connect-manager` with the Terraform output.
5. Create `staging` and `production` environments under GitHub repo settings; require manual approval for `production`.
6. Push to `aws-fargate` (or push a `v*` tag) to trigger the first deploy.
7. Verify ALB target health, run a /health probe, run a smoke import.
8. `terraform destroy` after verification.

Hard cap: $50 per smoke run. Any single resource that would push over (Multi-AZ RDS, large instance class) is gated behind a Lead approval.
