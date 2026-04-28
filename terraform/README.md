# terraform

Terraform that provisions the AWS substrate for the `aws-fargate` branch: VPC, ECS Fargate cluster, ALB, RDS Postgres, ElastiCache Valkey, Task Roles, ACM, ECR, Secrets Manager + KMS, CloudWatch alarms, the queue-depth metric publisher Lambda, and the GitHub Actions OIDC deploy role.

Each concern is a separately-destroyable module under `modules/`. The root composition in `main.tf` wires modules together; remote state is configured in `backend.tf` (operator-populated; not in version control).

For architecture and decisions, see `../SPEC.md`. For day-to-day rules, see `../CLAUDE.md`.

## Apply order

Terraform handles the actual ordering via dependency graph; this is the conceptual flow:

```
1.  vpc                 (VPC, public/private subnets, NAT)
2.  secrets             (CMK + Secrets Manager skeleton)
3.  ecr                 (image repos)
4.  acm-route53         (cert + DNS validation)
5.  ecs-cluster         (cluster + Fargate capacity providers)
6.  alb                 (LB + listener + target group + SG)
7.  Root-defined SGs    (web-tasks, worker-tasks, rds, valkey)
8.  rds                 (Postgres, depends on rds SG)
9.  elasticache         (Valkey, depends on valkey SG)
10. task-iam            (Task Execution Role + per-service Task Roles;
                         depends on ecr, secrets, rds, elasticache for ARN inputs)
11. queue-metric        (Lambda + EventBridge schedule;
                         depends on elasticache + secrets)
12. ecs-service-web     (depends on cluster + alb + task-iam + ecr + rds + elasticache + secrets)
13. ecs-service-worker  (same minus alb, plus queue-metric for autoscaling metric)
14. ecs-task-migrate    (depends on cluster + task-iam + ecr + rds)
15. alarms              (depends on cluster + rds + elasticache + alb + queue-metric)
16. github-oidc         (depends on cluster + task-iam, gated on var.github_repo)
```

## First-time setup

1. Create a Route53 hosted zone for `var.domain` (operator action; the module does NOT create the zone — it only adds validation records).
2. Create an S3 bucket + DynamoDB table for remote state, populate `terraform/backend.tf`.
3. Copy `terraform.tfvars.example` to `terraform.tfvars`; fill in `domain`, `route53_zone_id`, optionally `github_repo` and `alarms_email`.
4. `terraform init` — picks up the backend.
5. `terraform plan` — sanity-check.
6. `terraform apply` — ~25 minutes on a cold create (RDS + ALB are the long poles).
7. `gh secret set AWS_ROLE_ARN -R <org>/<repo>` with `terraform output -raw github_actions_role_arn`.
8. Trigger a CI deploy by pushing to `aws-fargate` (or pushing a `v*` tag).

## Local validation

`terraform fmt -recursive` + `terraform init -backend=false` + `terraform validate` runs cleanly without any AWS access. CI gates PRs on these.

A real `terraform apply` against ministack is out of scope for this codebase — ministack-light doesn't expose ECS, ALB, Lambda, EventBridge, or CloudWatch alarm APIs in the shape the modules expect. The local-stack workflow validates the Python app + data plane (via plain Postgres/Valkey containers + ministack Secrets Manager); the Terraform side is validated only by `terraform validate` until the real-AWS smoke task.

## Module index

| Module | Purpose |
|---|---|
| `vpc` | 3-AZ VPC, public + private subnets, single NAT |
| `ecs-cluster` | Cluster + FARGATE/FARGATE_SPOT capacity providers, Container Insights on |
| `alb` | Internet-facing ALB, HTTPS listener, web target group |
| `task-iam` | Task Execution Role + per-service Task Roles |
| `ecs-service-web` | Web Task Def (web + lucidlink-api sidecar) + Service + CPU autoscaling |
| `ecs-service-worker` | Worker Task Def + Service + custom-metric autoscaling |
| `ecs-task-migrate` | One-shot Alembic Task Def |
| `queue-metric` | Lambda that publishes ARQ queue depth → CloudWatch |
| `rds` | Postgres 16 instance + master credentials secret |
| `elasticache` | Valkey 8 replication group + AUTH bundle secret |
| `secrets` | CMK + Secrets Manager skeleton (jwt, admin) |
| `acm-route53` | ACM cert + Route53 validation |
| `ecr` | ECR repos for connect-web, connect-worker |
| `alarms` | CloudWatch alarms + SNS topic |
| `github-oidc` | OIDC provider + deploy role for GitHub Actions |

Each module has its own `README.md` documenting inputs, outputs, and the failure modes it covers.
