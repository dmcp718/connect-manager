# Cost estimate — `aws-fargate`

All figures are **us-east-1 list prices** as of 2026-Q2. Real bills depend on traffic, log volume, NAT data egress, and whether SPOT capacity is available.

## Tiers

### dev (single AZ, no HA, scale-to-zero)

For local-team validation — accept downtime during AZ outage.

| Component | Sizing | $/mo |
|---|---|---|
| ALB | 1× | $16.20 |
| RDS db.t4g.micro | single-AZ | $13 |
| ElastiCache cache.t4g.micro | single node | $13 |
| NAT Gateway | 1× | $32.40 |
| Web Fargate | 0.5 vCPU / 1 GiB, desired_count=1, 24×7 | $9 |
| lucidlink-api sidecar | 0.25 vCPU / 0.5 GiB, in same task | included |
| Worker Fargate SPOT | min=0, ~5% duty | <$1 |
| queue-metric Lambda | 43k invocations/mo | $0.20 |
| ECR storage (~5 GB) | | $0.50 |
| CloudWatch Logs | 14d retention, ~2 GB/mo | $1 |
| CloudWatch metrics + alarms | 7 alarms | $0.70 |
| Secrets Manager | 4 secrets | $1.60 |
| KMS CMK | 1 key | $1 |
| Route 53 | 1 hosted zone | $0.50 |
| **Total** | | **~$88** |

### prod (multi-AZ data plane, HA web)

Default tier — what `terraform apply` produces with `terraform.tfvars.example` defaults plus `rds_multi_az = true`.

| Component | Sizing | $/mo |
|---|---|---|
| ALB | 1× | $16.20 |
| RDS db.t4g.micro **Multi-AZ** | | $26 |
| ElastiCache cache.t4g.micro | single node + 1 replica | $26 |
| NAT Gateway | 1× | $32.40 |
| Web Fargate | 0.5 vCPU / 1 GiB, desired=2, 24×7 | $18 |
| lucidlink-api sidecar (×2 web tasks) | included | included |
| Worker Fargate SPOT | avg ~1× during bursts | $3 |
| queue-metric Lambda | as above | $0.20 |
| ECR storage | | $1 |
| CloudWatch Logs | ~10 GB/mo | $5 |
| CloudWatch metrics + alarms | | $1 |
| Secrets Manager | 4 secrets | $1.60 |
| KMS CMK | | $1 |
| Route 53 | | $0.50 |
| **Total** | | **~$132** |

### prod-ha (multi-AZ everything, no SPOT, larger sizing)

For production deployments with strict SLA. Override:
- `rds_instance_class = "db.t4g.small"`, `rds_multi_az = true`
- `elasticache_node_type = "cache.t4g.small"`
- web: `web_min_count = 3`, `web_max_count = 12`, vCPU 1.0, mem 2 GiB
- worker: `use_fargate_spot = false`, `worker_min_count = 1`

| Component | Sizing | $/mo |
|---|---|---|
| ALB | 1× | $16.20 |
| RDS db.t4g.small **Multi-AZ** | | $52 |
| ElastiCache cache.t4g.small × 2 | | $52 |
| NAT Gateway | 1× | $32.40 |
| Web Fargate (on-demand) | 1.0 vCPU / 2 GiB × 3 baseline, 24×7 | $54 |
| lucidlink-api sidecar (×3) | 0.25 / 0.5 each | included in task sizing |
| Worker Fargate (on-demand) | min=1, avg 2× | $30 |
| queue-metric Lambda | | $0.20 |
| ECR storage | | $1 |
| CloudWatch Logs | ~30 GB/mo | $15 |
| CloudWatch metrics + alarms | | $1 |
| Secrets Manager | | $1.60 |
| KMS CMK | | $1 |
| Route 53 | | $0.50 |
| **Total** | | **~$257** |

## Comparison vs alternatives

| Tier | Stack | $/mo | Multi-AZ HA | Worker autoscale |
|---|---|---|---|---|
| `aws-deploy` | EC2 + Compose + EFS | ~$37 | No | No |
| `aws-deploy` v2 (hypothetical pivot) | EC2 + Compose + RDS | ~$50 | No | No |
| **`aws-fargate` dev** | ECS Fargate | ~$88 | No | Yes |
| **`aws-fargate` prod** | ECS Fargate | ~$132 | Yes (data plane) | Yes |
| **`aws-fargate` prod-ha** | ECS Fargate, on-demand | ~$257 | Yes everywhere | Yes |
| `aws-kubernetes` (abandoned) | EKS Auto Mode | ~$175–230 | Yes | Yes (KEDA) |

## Cost levers (in order of impact)

1. **NAT Gateway** ($32.40 fixed). VPC endpoints for S3, ECR, Secrets Manager, CloudWatch Logs (~$7/endpoint/mo + $0.01/GB) can replace it for in-VPC AWS calls. Pays back at ~5 GB/day egress.
2. **RDS Multi-AZ** ($13). Single-AZ has ~30s failover but no automated cross-AZ — RPO is the latest snapshot. Acceptable for dev, not for prod.
3. **ElastiCache** ($13/node). Could be removed for non-ARQ workloads. ARQ requires Redis-compatible.
4. **Worker on-demand vs SPOT** ($30 vs $3 at avg 1× duty). Worth it only when SPOT interruption is operationally inconvenient — most workloads are fine on SPOT given idempotent jobs.
5. **Web replicas**. The 2-replica baseline is for AZ HA, not throughput. CPU autoscaling handles spikes; baseline can drop to 1 if AZ HA isn't a hard requirement (dev only).

## What's NOT in these numbers

- Cross-AZ data transfer between Fargate tasks and RDS / ElastiCache (typically <$5/mo at this scale).
- NAT data egress beyond ECR pulls and metrics emission. If the worker calls customer S3 buckets in other accounts, the cross-account egress is on this account.
- ECR push from CI (free for OIDC-authenticated pushes; the ECR-side data is included in the storage line).
- The S3 bucket + DynamoDB table for Terraform state (a few cents/month).
- One-time costs (operator time, smoke run).
