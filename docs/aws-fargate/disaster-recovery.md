# Disaster recovery runbook — `aws-fargate`

This document covers recovery scenarios for the `aws-fargate` deployment. The stack is single-region by design; multi-region is an open ADR, not a current capability.

## Recovery objectives

| Tier | RPO | RTO | Notes |
|---|---|---|---|
| **dev** | 24 hours | 1 hour | Acceptable to lose a day of activity logs / job state. |
| **prod** | 5 minutes (RDS PITR) | 30 minutes | Multi-AZ data plane required. |
| **prod-ha** | 5 minutes (RDS PITR) | 15 minutes | Pre-warmed standby tasks; PITR restore + service redeploy. |

RPO/RTO are aspirational targets; they have not been measured against the live deployment yet. The Phase 7 real-AWS smoke run includes a tabletop DR exercise.

## Backups

| Resource | Backup mechanism | Default retention |
|---|---|---|
| RDS Postgres | Automated daily snapshots + point-in-time recovery | 7 days (set in `terraform/modules/rds/main.tf` via `backup_retention_period = 7`) |
| ElastiCache Valkey | Daily snapshots | 1 day (set in `terraform/modules/elasticache/main.tf` via `snapshot_retention_limit = 1`) |
| Secrets Manager | Version history (automatic) | 30 days standard |
| ECR images | Versioned by image tag (commit SHA or `v*`) | Retained until lifecycle policy expires (no policy currently) |
| Application logs | CloudWatch Logs | 14 days for ECS task logs, 7 days for Lambda |
| Terraform state | S3 versioning + DynamoDB lock | Bucket-versioning retention (operator-managed) |

## Scenario: accidental RDS deletion

**Symptom:** RDS instance deleted via console or `terraform destroy`. The `final_snapshot_identifier` on the instance ensures a final snapshot exists.

**Recovery steps:**

1. Find the final snapshot:
   ```bash
   aws rds describe-db-snapshots \
     --snapshot-type manual \
     --query 'DBSnapshots[?starts_with(DBSnapshotIdentifier, `connect-prod-final-`)] | sort_by(@, &SnapshotCreateTime) | [-1]'
   ```
2. Restore from snapshot to a new instance with the *same identifier* the Terraform module expects (`connect-<env>`):
   ```bash
   aws rds restore-db-instance-from-db-snapshot \
     --db-instance-identifier connect-prod \
     --db-snapshot-identifier <snapshot-id> \
     --vpc-security-group-ids <rds-sg-id> \
     --db-subnet-group-name connect-prod
   ```
3. Re-import to Terraform state:
   ```bash
   terraform import module.rds.aws_db_instance.this connect-prod
   ```
4. `terraform plan` to verify state is consistent. Expect drift on master password (RDS doesn't expose it; the secret in Secrets Manager retains the original).
5. The DATABASE_URL in `connect/<env>/db` Secrets Manager secret has a hardcoded host — update it to the new endpoint:
   ```bash
   new_endpoint=$(aws rds describe-db-instances --db-instance-identifier connect-prod \
     --query 'DBInstances[0].Endpoint.Address' --output text)
   # Read current secret, swap host, write back
   ```
6. `aws ecs update-service --force-new-deployment` for web + worker so they pick up the new secret.

## Scenario: accidental Secrets Manager deletion / corruption

**Symptom:** A secret value (e.g., `connect/prod/jwt`) was overwritten or deleted.

**Recovery steps:**

1. List versions:
   ```bash
   aws secretsmanager list-secret-version-ids --secret-id connect/prod/jwt
   ```
2. Restore the previous `AWSPREVIOUS` stage:
   ```bash
   aws secretsmanager update-secret-version-stage \
     --secret-id connect/prod/jwt \
     --version-stage AWSCURRENT \
     --remove-from-version-id <bad-version> \
     --move-to-version-id <good-version>
   ```
3. Restart web + worker tasks to pick up the previous value:
   ```bash
   aws ecs update-service --cluster connect-prod --service connect-prod-web --force-new-deployment
   aws ecs update-service --cluster connect-prod --service connect-prod-worker --force-new-deployment
   ```

If the secret is `connect/prod/jwt` (the JWT signing key), all currently-issued tokens become invalid — users must log in again. This is intentional; rotating the JWT key is the same operation as recovering from compromise.

## Scenario: ALB or web service down (region partial outage)

**Symptom:** ALB target health all red; ECS console shows web tasks not starting.

**Triage:**

1. `aws ecs describe-services --cluster connect-prod --services connect-prod-web --query 'services[0].events[:10]'` — look for AZ-specific complaints.
2. `aws ec2 describe-availability-zones --region us-east-1 --query 'AvailabilityZones[?State!=`available`]'` — confirm AWS-side AZ status.
3. Check the AWS Status page (status.aws.amazon.com) for us-east-1 ALB / ECS / Fargate incidents.

**Mitigation if AZ-specific:**

The VPC has 3 private subnets across 3 AZs; ECS will route around a failed AZ if subnet placement allows. If two AZs go, drop affected subnets from the service:

```bash
aws ecs update-service --cluster connect-prod --service connect-prod-web \
  --network-configuration "awsvpcConfiguration={subnets=[<healthy-subnet-1>,<healthy-subnet-2>],securityGroups=[<web-tasks-sg>],assignPublicIp=DISABLED}"
```

## Scenario: full region out

**Symptom:** us-east-1 unreachable. RDS, ECS, ALB, ECR, Secrets Manager — all unavailable.

**Recovery (manual, no automation today):**

1. Pick a target region (e.g., us-west-2).
2. Restore RDS from a cross-region copied snapshot. RDS automated snapshots are *not* cross-region by default — operator must enable copy or accept a longer RPO using the latest manual snapshot.
3. `terraform apply` against a new state file in the target region with the same `terraform.tfvars` (var.aws_region = us-west-2). This creates a parallel stack — VPC, ECS cluster, ALB, etc.
4. Restore Secrets Manager values manually (cross-region replication not enabled by default).
5. Push a Route53 record change to point `var.domain` at the new ALB.
6. Run migrations against the restored RDS, then `update-service` to start tasks.

**Estimated RTO:** 4-8 hours from operator awareness to user-visible recovery.

This is acknowledged as a gap. A multi-region ADR is open in SPEC.md §3 ("Open questions") — addressing it requires:
- RDS cross-region read replica (~$26/mo extra for the standby + transfer)
- Secrets Manager cross-region replication
- Route53 health-checked DNS failover
- Cross-region ECR pull-through (or duplicate registry)

## Scenario: accidental `terraform destroy`

**Mitigation BEFORE the fact:**

The S3 state bucket should have:
- Versioning **enabled**
- Object lock or replication to a sister bucket
- Bucket policy denying `s3:DeleteObjectVersion` to non-Lead principals

`terraform destroy` requires `terraform apply`-equivalent IAM. The github-oidc deploy role does NOT have full destroy permissions — only ECS UpdateService. Manual destroys require a privileged operator session.

**Recovery if it happens:**

1. RDS final snapshot: see "accidental RDS deletion" above.
2. ElastiCache: snapshot retained 1 day; if within window, restore via `aws elasticache create-replication-group --snapshot-name <last-snapshot>`. Otherwise, ARQ + activity logs are lost — fresh Valkey starts empty, in-flight jobs are dropped.
3. ECR images: ECR is not deleted by `terraform destroy` of the connect-* repos *unless* `force_delete = true` was set. Default is to refuse if images exist, so you can re-import.
4. Secrets Manager: secrets are scheduled for deletion with a 30-day window by default. `aws secretsmanager restore-secret --secret-id <arn>` undoes the deletion if within the window.

## Verification checklist (post-recovery)

After any recovery action, verify:

- [ ] `curl -fsS https://<domain>/health` returns 200 with `{"status":"ok"}`
- [ ] `curl -fsS https://<domain>/ready` returns 200 with `{"status":"ok"}`
- [ ] Web container logs show no crash loops in the last 5 min
- [ ] ALB target health shows ≥1 healthy target per AZ
- [ ] Worker can pick up a job (smoke-test by enqueuing one via `/api/import/file`)
- [ ] CloudWatch alarms not firing
- [ ] Sample read of customer data via the UI succeeds

## Tabletop drill cadence

Recommend exercising the **accidental Secrets Manager deletion** and **accidental RDS deletion** scenarios at least once per quarter against the dev tier. The full region-out scenario is hard to drill without dual-region infrastructure; for now, validate the runbook by walking through it step by step on paper.
