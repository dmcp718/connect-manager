# terraform/modules/alarms

Self-contained Terraform module that provisions the `connect-alerts` SNS topic
and every CloudWatch alarm required by `SPEC.md` §8.3. All alarms target the
SNS topic for fan-out; subscription targets (PagerDuty, Slack webhook) are
operator-configured out of band, with a convenience email subscription wired
through `var.sns_topic_subscription_email`.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `cluster_name` | `string` | — | EKS cluster name; ClusterName dimension on Container Insights metrics. |
| `db_instance_id` | `string` | — | RDS DB instance identifier (DBInstanceIdentifier dimension). |
| `cache_cluster_id` | `string` | — | ElastiCache replication group ID (ReplicationGroupId dimension). |
| `alb_arn_suffix` | `string` | `""` | ALB metric dimension suffix `app/<lb-name>/<lb-id>`. Empty disables the ALB alarm. |
| `namespace` | `string` | `"connect"` | Kubernetes namespace for pod-restart alarms. |
| `prometheus_namespace` | `string` | `"Connect/ARQ"` | CloudWatch namespace where the Prometheus bridge republishes `queue_depth`. |
| `sns_topic_subscription_email` | `string` | `""` | If non-empty, subscribe this address to `connect-alerts`. |
| `tags` | `map(string)` | `{}` | Additional tags merged onto every taggable resource. |

## Outputs

| Name | Description |
|---|---|
| `sns_topic_arn` | ARN of the `connect-alerts` SNS topic. |
| `alarm_arns` | Map of `alarm_name → alarm_arn` for every alarm created. |

## Alarms (matching SPEC §8.3 exactly)

| Alarm | Metric | Threshold | Window | Statistic |
|---|---|---|---|---|
| `connect-alb-5xx-rate` | `(HTTPCode_Target_5XX_Count / RequestCount) * 100` | > 1% | 5m | Sum / Sum, math expression |
| `connect-rds-cpu-high` | `AWS/RDS CPUUtilization` | > 80% | 10m (2 × 5m) | Average |
| `connect-rds-free-storage-low` | `AWS/RDS FreeStorageSpace` | < 5 GiB | 5m | Average |
| `connect-elasticache-cpu-high` | `AWS/ElastiCache CPUUtilization` | > 75% | 10m (2 × 5m) | Average |
| `connect-worker-queue-depth-high` | `Connect/ARQ queue_depth` | > 100 | 15m (3 × 5m) | Maximum |
| `connect-pod-restart-web` | `ContainerInsights pod_number_of_container_restarts` | > 5/h | 1h | Sum |
| `connect-pod-restart-worker` | `ContainerInsights pod_number_of_container_restarts` | > 5/h | 1h | Sum |

The ALB alarm is created only when `alb_arn_suffix` is non-empty so the module
remains usable in environments where the ingress isn't yet provisioned (e.g.
local stacks, partial bring-up).

## ALB metric_query pattern

CloudWatch alarms can't natively compute percentages from a single metric, so
the 5xx-rate alarm uses three `metric_query` blocks:

- `m1` → `AWS/ApplicationELB HTTPCode_Target_5XX_Count` (Sum, 300s)
- `m2` → `AWS/ApplicationELB RequestCount` (Sum, 300s)
- `e1` → `(m1 / m2) * 100` — the value the alarm threshold is evaluated against

Both source metrics use `LoadBalancer = var.alb_arn_suffix` as the dimension.
The arn-suffix form is `app/<lb-name>/<lb-id>` — i.e. the trailing portion of
the ALB ARN after `:loadbalancer/`. The ALB module exposes this directly.

## Prometheus → CloudWatch bridge for queue_depth

The `connect_arq_queue_depth` Prometheus gauge is shipped by the worker and
exposed on `/metrics`. CloudWatch can't scrape Prometheus endpoints directly,
so a bridge component must republish that metric into the CloudWatch
namespace this module reads from (default `Connect/ARQ`, metric name
`queue_depth`).

The expected operational setup is one of:

- **ADOT (AWS Distro for OpenTelemetry) Collector** with a `prometheus`
  receiver scraping the worker pods and an `awsemf` exporter writing to the
  configured namespace.
- **CloudWatch Agent** running with `prometheus_exporter` scrape config — the
  EKS-on-Fargate or Container Insights pattern documented by AWS.

If neither is yet deployed in the cluster, the alarm will sit in
`INSUFFICIENT_DATA` (treated as not breaching by `treat_missing_data`). File
a Lead bead to wire up the bridge as a follow-up; the alarm will start firing
correctly once the metric appears in the namespace.

## Email subscription pre-requirement

When `sns_topic_subscription_email` is non-empty, AWS sends a confirmation
email to that address. **The operator must click the confirmation link** —
until then, AWS reports the subscription as `PendingConfirmation` and no
alerts will be delivered to that address. This is a property of the SNS
service, not the Terraform module.

Other subscription types (PagerDuty https endpoint, Slack-via-Lambda, SMS,
chatbot) are out of scope here and added by the operator once the topic ARN
is known (`module.alarms.sns_topic_arn`).

## Usage from `terraform/main.tf`

```hcl
module "alarms" {
  source = "./modules/alarms"

  cluster_name     = module.eks.cluster_name
  db_instance_id   = module.rds.db_instance_id
  cache_cluster_id = module.elasticache.replication_group_id
  alb_arn_suffix   = module.alb.arn_suffix # optional

  sns_topic_subscription_email = var.sns_topic_subscription_email

  tags = local.tags
}
```
