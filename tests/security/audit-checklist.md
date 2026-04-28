# Security audit checklist — `aws-fargate`

Pre-prod review for the CONNECT Manager Fargate stack. Run end-to-end during the real-AWS smoke (awsk-3r4.5) and again before any production deploy. Each item is yes/no/N/A; document findings and link to remediation beads.

## 1. Network

| # | Check | Method |
|---|---|---|
| 1.1 | ALB allows only 80/443 from the configured `var.alb_ingress_cidrs` | `aws ec2 describe-security-groups --group-names connect-prod-alb` |
| 1.2 | ALB → target redirect rules force HTTPS (80 → 301 → 443) | `aws elbv2 describe-listeners --load-balancer-arn $ALB_ARN` |
| 1.3 | TLS policy is `ELBSecurityPolicy-TLS13-1-2-2021-06` or stricter | same |
| 1.4 | Web tasks SG ingress: only ALB SG on `var.web_target_port` | `aws ec2 describe-security-groups --group-names connect-prod-web-tasks` |
| 1.5 | Worker tasks SG: no ingress rules (egress only) | same for `connect-prod-worker-tasks` |
| 1.6 | RDS SG ingress: only web-tasks + worker-tasks SGs on 5432 | `connect-prod-rds` |
| 1.7 | Valkey SG ingress: only web-tasks + worker-tasks + queue-metric Lambda SGs on 6379 | `connect-prod-valkey` |
| 1.8 | Public subnets have no Fargate tasks (web/worker run in private only) | `aws ecs describe-services --query 'services[].networkConfiguration.awsvpcConfiguration.subnets'` cross-ref VPC subnet IDs |
| 1.9 | NAT Gateway egress traffic only — no inbound IGW route on private RT | `aws ec2 describe-route-tables --filter Name=tag:Name,Values=connect-prod-private-rt` |
| 1.10 | RDS not publicly accessible | `aws rds describe-db-instances --query 'DBInstances[0].PubliclyAccessible'` → `false` |
| 1.11 | ElastiCache not publicly accessible | implicit — ElastiCache replication groups have no public-IP option |

## 2. IAM (Task Execution + Task Roles)

| # | Check | Method |
|---|---|---|
| 2.1 | Task Execution Role policy: ECR pull scoped to `connect-*` repos only | `aws iam get-role-policy --role-name connect-prod-task-execution --policy-name connect-prod-task-execution` |
| 2.2 | Task Execution Role: `secretsmanager:GetSecretValue` scoped to listed ARNs | same |
| 2.3 | Task Execution Role: `kms:Decrypt` scoped to the connect-secrets CMK ARN | same |
| 2.4 | Task Execution Role: log-group ARN scoped to `/aws/ecs/connect-prod/*` | same |
| 2.5 | Task Roles (`web`, `worker`, `lucidlink_api`) have only `cloudwatch:PutMetricData` with namespace condition | `aws iam get-role-policy --role-name connect-prod-task-{web,worker}` |
| 2.6 | `lucidlink_api` Task Role has zero inline policies (or only audit-tagging stubs) | `aws iam list-role-policies --role-name connect-prod-task-lucidlink-api` |
| 2.7 | No Task Role grants `*` on any AWS action | grep policy JSON for `"Action": "*"` |
| 2.8 | Trust policies require `sts:SourceArn` + `sts:SourceAccount` (confused-deputy) | `aws iam get-role --role-name connect-prod-task-execution` → AssumeRolePolicyDocument |

## 3. GitHub Actions OIDC deploy role

| # | Check | Method |
|---|---|---|
| 3.1 | OIDC trust restricted to `repo:dmcp718/connect-manager:ref:refs/heads/aws-fargate` and `refs/tags/v*` | `aws iam get-role --role-name connect-github-actions` |
| 3.2 | ECR push scoped to `connect-*` repos | get-role-policy on inline policy |
| 3.3 | ECS mutating actions scoped to the cluster ARN + sibling resource ARNs | same |
| 3.4 | `iam:PassRole` conditioned on `iam:PassedToService=ecs-tasks.amazonaws.com` | same |
| 3.5 | `iam:PassRole` resources limited to the four task roles (no wildcard) | same |
| 3.6 | No `iam:CreateRole`, `iam:PutRolePolicy`, `iam:AttachRolePolicy` granted | grep |

## 4. Secrets

| # | Check | Method |
|---|---|---|
| 4.1 | Customer-managed KMS key encrypts all 4 connect secrets (jwt, admin, db, valkey) | `aws secretsmanager describe-secret --secret-id …` for each, check `KmsKeyId` |
| 4.2 | KMS key has rotation enabled (1y or shorter) | `aws kms get-key-rotation-status --key-id …` |
| 4.3 | KMS key policy grants account root + denies `*:Decrypt` from outside-account principals | `aws kms get-key-policy --key-id … --policy-name default` |
| 4.4 | RDS storage encrypted (`storage_encrypted = true`) | `aws rds describe-db-instances --query 'DBInstances[0].StorageEncrypted'` |
| 4.5 | ElastiCache `at_rest_encryption_enabled = true` and `transit_encryption_enabled = true` | `aws elasticache describe-replication-groups` |
| 4.6 | Secrets not visible in Task Definition `environment` (only `secrets` valueFrom) | `aws ecs describe-task-definition --task-definition connect-prod-web --query 'taskDefinition.containerDefinitions[]'` |
| 4.7 | No `aws_access_key_id` / `aws_secret_access_key` env vars on any container | same |
| 4.8 | JWT secret value is ≥32 random bytes (operator confirms during seed) | manual check during the bootstrap-TUI step 5 |

## 5. Compute

| # | Check | Method |
|---|---|---|
| 5.1 | `enable_execute_command = false` on both web and worker services (no `ecs:ExecuteCommand`) | `aws ecs describe-services --query 'services[].enableExecuteCommand'` |
| 5.2 | Tasks launched with `assignPublicIp=DISABLED` | `aws ecs describe-services --query 'services[].networkConfiguration.awsvpcConfiguration.assignPublicIp'` |
| 5.3 | Container images pulled from ECR via OCI digest, not just `latest` (post-deploy) | `aws ecs describe-task-definition --query 'taskDefinition.containerDefinitions[].image'` should show `…@sha256:…` after CI deploys |
| 5.4 | ECR scan-on-push enabled | `aws ecr describe-repositories --query 'repositories[].imageScanningConfiguration'` |
| 5.5 | ECR `imageTagMutability=IMMUTABLE` | same query, `imageTagMutability` field |
| 5.6 | Worker `stopTimeout` matches longest-expected-job (default 120s) | `aws ecs describe-task-definition --query 'taskDefinition.containerDefinitions[?name==`worker`].stopTimeout'` |

## 6. Logging + observability

| # | Check | Method |
|---|---|---|
| 6.1 | All ECS tasks ship to `/aws/ecs/connect-prod/*` log groups | `aws logs describe-log-groups --log-group-name-prefix /aws/ecs/connect-prod` |
| 6.2 | Log groups have a retention policy set (not infinite) | same query, `retentionInDays` field |
| 6.3 | Container Insights enabled on the ECS cluster | `aws ecs describe-clusters --include SETTINGS --clusters connect-prod` |
| 6.4 | RDS `enabled_cloudwatch_logs_exports` = `["postgresql"]` | `aws rds describe-db-instances --query 'DBInstances[0].EnabledCloudwatchLogsExports'` |
| 6.5 | CloudWatch alarms have an SNS topic subscription | `aws sns list-subscriptions-by-topic --topic-arn $ALARMS_SNS_ARN` |

## 7. Application boundary

| # | Check | Method |
|---|---|---|
| 7.1 | `services/aws.py` rejects explicit AWS credentials (`_FORBIDDEN_KWARGS`) | grep `_check_no_explicit_creds` in `app/services/aws.py`; add a unit test asserting `ValueError` if missing |
| 7.2 | Customer-credential code paths (`s3_service.py`, `sqs_service.py`) accept explicit keys (intentional) | grep their constructors |
| 7.3 | Customer creds are Fernet-encrypted at rest in Postgres | grep `Fernet(` in `app/services/secrets.py` + verify the column type |
| 7.4 | JWT signing key derived from `JWT_SECRET_KEY` env var (Secrets Manager-sourced) | grep `jwt.encode` / `jwt.decode` in `app/services/auth.py` |
| 7.5 | Cookies marked `Secure`, `HttpOnly`, `SameSite=lax` | `app/services/auth.py` `set_cookie` calls |
| 7.6 | No `print()` of credentials anywhere in the app | `grep -rE "print\(.*(secret\|token\|password\|key)" app/` (false positives expected; manual review) |

## 8. Operational

| # | Check | Method |
|---|---|---|
| 8.1 | `terraform.tfvars` not committed (gitignored) | `git check-ignore -v terraform/terraform.tfvars` |
| 8.2 | S3 state bucket has versioning enabled + DeleteObjectVersion denied | `aws s3api get-bucket-versioning --bucket $TFSTATE_BUCKET` and bucket policy |
| 8.3 | DynamoDB lock table point-in-time recovery enabled | `aws dynamodb describe-continuous-backups` |
| 8.4 | RDS `deletion_protection = true` (terraform/modules/rds/main.tf default) | `aws rds describe-db-instances --query 'DBInstances[0].DeletionProtection'` |
| 8.5 | RDS `backup_retention_period >= 7` days | same query, `BackupRetentionPeriod` |
| 8.6 | `final_snapshot_identifier` set on RDS so `terraform destroy` produces a snapshot | grep `final_snapshot_identifier` in `terraform/modules/rds/main.tf` (already set; verify post-apply) |
| 8.7 | Production GitHub environment requires manual approval | repo Settings → Environments → production → Required reviewers ≥ 1 |

## Output template

```
# Audit run: <date>, env <prod>, commit <sha>
| # | Check | Result | Evidence | Bead |
|---|---|---|---|---|
| 1.1 | ALB ingress … | ✓ | `aws ec2 describe-…` output | — |
| 2.7 | No '*' actions | ✗ | `connect-prod-task-web` has `cloudwatch:*` on namespace condition only | awsk-XXX |
…
```

Findings get filed as P1/P2 bugs. Audit cannot pass until all P1 items are clean and P2 items have a remediation bead with an owner.
