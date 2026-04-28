# Operator deployment runbook — `aws-fargate`

This runbook walks through deploying the `aws-fargate` stack from scratch. The TUI in `bootstrap/` (`connect-bootstrap`) is the canonical path; the manual steps below are the fallback when the TUI isn't usable (CI runs, headless environments).

For architecture context, see `../../SPEC.md`. For per-module detail, see `../../terraform/modules/<name>/README.md`.

## TUI path (canonical)

```bash
pip install -e ./bootstrap
connect-bootstrap
```

Walks through:

1. Dependency check (aws / terraform / jq / gh)
2. AWS authentication (env / SSO / ministack)
3. `terraform.tfvars` form
4. `terraform plan` + confirm + `terraform apply` with live output
5. Secrets Manager seeder (jwt + admin) — `Generate` button for the JWT
6. ECS cluster verification (`describe-clusters`)
7. CONNECT install (`update-service` + ALB target health smoke probe)
8. Status dashboard (auto-refresh every 15s)

Dry-run against ministack (no real AWS) for testing the wizard:

```bash
AWS_ENDPOINT_URL=http://localhost:4566 \
AWS_ACCESS_KEY_ID=test \
AWS_SECRET_ACCESS_KEY=test \
connect-bootstrap
```

Not every step works against ministack-light (ECS / ALB / Auto Scaling are mostly stubbed); use the real AWS path for the actual smoke run.

## Manual fallback path

If the TUI isn't an option, run the steps below directly.

## Pre-requisites

| Requirement | How |
|---|---|
| AWS account + admin-equivalent IAM user/role for the apply | Operator-managed (out of scope here). |
| AWS CLI v2 + Terraform 1.5+ + jq + gh | `aws --version`, `terraform version`, `jq --version`, `gh --version`. |
| Route53 hosted zone for `var.domain` | Create + delegate before apply. The `acm-route53` module does NOT create the zone — it only adds validation records. |
| S3 bucket + DynamoDB table for remote state | Create out of band; populate `terraform/backend.tf` (not committed). |
| GitHub repo `dmcp718/connect-manager` exists with the `aws-fargate` branch pushed | Already done. |

## One-time bootstrap

### 1. Configure AWS access

```bash
aws sso login --profile connect-prod   # or whatever profile name you use
export AWS_PROFILE=connect-prod
aws sts get-caller-identity            # sanity check — should print the deploy account
```

### 2. Populate `terraform/backend.tf`

Create the file (gitignored) with:

```hcl
terraform {
  backend "s3" {
    bucket         = "your-tfstate-bucket"
    key            = "lucidlink-connect/aws-fargate/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "your-tflock-table"
    encrypt        = true
  }
}
```

### 3. Populate `terraform/terraform.tfvars`

Copy `terraform.tfvars.example` to `terraform.tfvars` and fill in:

```hcl
domain          = "connect.example.com"
route53_zone_id = "Z0123456789ABCDEFGHIJ"
github_repo     = "dmcp718/connect-manager"
alarms_email    = "alerts@example.com"
```

Override any defaults you want (instance classes, scale ranges, etc.). The example file documents every knob.

### 4. First apply

```bash
cd terraform
terraform init
terraform plan      # sanity check — should show ~80 resources
terraform apply     # ~25 minutes (RDS + ALB are the long poles)
```

If apply errors out partway, fix and re-run — Terraform converges. The slow resources (RDS, ALB target group, ACM cert validation) are idempotent.

### 5. Push the GitHub Actions deploy role to repo secrets

```bash
gh secret set AWS_ROLE_ARN -R dmcp718/connect-manager \
  -b "$(terraform output -raw github_actions_role_arn)"
```

### 6. Create the `staging` and `production` GitHub environments

GitHub repo → Settings → Environments → New environment:

- **`staging`** — no protection rules. Receives auto-deploys on push to `aws-fargate`.
- **`production`** — required reviewers: Lead. Receives auto-deploys on `v*` tag pushes; pauses for approval before running.

### 7. Trigger the first CI deploy

```bash
git push github aws-fargate
gh run watch -R dmcp718/connect-manager
```

The workflow runs:
1. `lint-test` (ruff + mypy + pytest)
2. `terraform` (fmt + validate)
3. `build-push` (multi-arch buildx → ECR for connect-web + connect-worker)
4. `migrate` (registers a new revision of the migrate task def with the new image, runs it, waits for success)
5. `deploy` (registers new web + worker task def revisions, `aws ecs update-service --force-new-deployment`, `aws ecs wait services-stable`)

### 8. Verify

```bash
# Web target health
aws elbv2 describe-target-health \
  --target-group-arn $(aws elbv2 describe-target-groups \
    --names connect-prod-web --query 'TargetGroups[0].TargetGroupArn' --output text)

# Health probe via ALB
curl -fsSv "https://$(terraform output -raw app_url | sed 's|https://||')/health"

# Web task logs
aws logs tail /aws/ecs/connect-prod/web --since 5m --follow

# Worker task logs (should be empty when no jobs queued)
aws logs tail /aws/ecs/connect-prod/worker --since 5m --follow
```

## Production tag deploy

Triggered by pushing a `v*` tag:

```bash
git tag -a v0.1.0 -m "First production deploy"
git push github v0.1.0
```

Watch the workflow; it will pause at the `deploy` job waiting for manual approval in the `production` environment. Approver reviews the diff (commits since last tag, image scan results from ECR if enabled) and approves.

## Updates without a CI deploy

Day-to-day code changes flow through CI. Manual interventions:

- **Image hotfix** (build + push manually):
  ```bash
  aws ecr get-login-password --region us-east-1 \
    | docker login --username AWS --password-stdin <ecr-uri>
  docker buildx build --platform linux/amd64,linux/arm64 \
    --target web -t <ecr-uri>/connect-web:hotfix-1 --push .
  aws ecs register-task-definition --cli-input-json "$(aws ecs describe-task-definition \
    --task-definition connect-prod-web --query 'taskDefinition' \
    | jq --arg img "<ecr-uri>/connect-web:hotfix-1" \
        '(.containerDefinitions[] | select(.name=="web")).image = $img
         | del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
               .compatibilities, .registeredAt, .registeredBy)')"
  aws ecs update-service --cluster connect-prod --service connect-prod-web \
    --task-definition connect-prod-web --force-new-deployment
  ```

- **Scale up under load** (without code change):
  ```bash
  aws application-autoscaling register-scalable-target \
    --service-namespace ecs --resource-id service/connect-prod/connect-prod-web \
    --scalable-dimension ecs:service:DesiredCount --min-capacity 4 --max-capacity 12
  ```

- **Rollback to previous task definition revision**:
  ```bash
  aws ecs update-service --cluster connect-prod --service connect-prod-web \
    --task-definition connect-prod-web:<previous-revision-number> \
    --force-new-deployment
  aws ecs wait services-stable --cluster connect-prod --services connect-prod-web
  ```

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| `terraform apply` hangs on `aws_acm_certificate_validation` | DNS validation records pending | Verify the hosted zone is delegated; `dig NS <domain>` should return the zone's name servers. Wait — validation can take 5-15 min. |
| Web tasks stuck in `PROVISIONING` | Subnet capacity or pull failures | `aws ecs describe-services --cluster connect-prod --services connect-prod-web --query 'services[0].events[0:5]'` for the recent error. |
| 503 from ALB despite running tasks | Target group health check failing | Check the web container is binding 0.0.0.0:8000 and `/health` returns 200. Confirm web-tasks SG allows ingress from ALB SG on 8000. |
| Worker not scaling out under load | `queue-metric` Lambda failing | `aws logs tail /aws/lambda/connect-prod-queue-metric --since 10m`. Most likely: Valkey reachability (check Lambda's SG vs Valkey SG ingress rule). |
| Migration task fails with auth error | Secrets Manager rotation lag | Re-run with `--force` or wait 60s and retry. The DATABASE_URL is rebuilt from the live RDS endpoint via the secret value, so cold rotates take effect after a fresh task start. |

## Teardown

```bash
cd terraform
terraform destroy   # ~10 minutes
```

`final_snapshot_identifier` on RDS uses a timestamp, so the snapshot will survive even if you intend a full delete. Manually delete the snapshot via the RDS console if you don't need it.

The S3 state bucket and DynamoDB lock table are not managed by this Terraform — clean up out of band if needed.
