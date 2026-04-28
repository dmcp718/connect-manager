# terraform/modules/queue-metric

EventBridge-driven Lambda that publishes ARQ queue depth to CloudWatch every minute. Foundational for worker scale-to-zero — the workers themselves can't emit the metric while scaled to zero, which is exactly when we need it to scale them back up.

## Why a Lambda

| Approach | Why not |
|---|---|
| Worker emits its own metric | Breaks scale-to-zero (no worker → no metric → no scale-up). |
| Web emits the metric | Couples web to worker observability; works only as long as web is multi-replica. |
| **Standalone Lambda** | Single point of emission, runs even when both services are scaled down, costs ~$0.20/mo at 1/min. |

## What it does

1. EventBridge schedule fires at `var.schedule_expression` (default `rate(1 minute)`).
2. Lambda fetches the Valkey AUTH bundle from `var.valkey_secret_arn` (cached across warm invocations).
3. Opens a TLS connection to Valkey, sends `AUTH default <token>` then `LLEN <queue_key>`.
4. Publishes one CloudWatch datapoint: `Connect/ARQ.connect_arq_queue_depth` with dimensions `{Env, Service=worker}`.

## Implementation note

The Lambda is pure stdlib + boto3 (no `redis-py` dep) — it speaks just enough RESP to do `AUTH` and `LLEN`. Avoids needing a layer or a build step.

## Inputs

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `env` | `string` | yes | — | Becomes the `Env` metric dimension. |
| `aws_region` | `string` | yes | — | Log-group ARN scoping. |
| `valkey_secret_arn` | `string` | yes | — | `module.elasticache.auth_secret_arn`. |
| `kms_key_arn` | `string` | yes | — | The connect-secrets CMK ARN. |
| `vpc_id` | `string` | yes | — | Lambda runs in-VPC so it can reach private-subnet Valkey. |
| `private_subnet_ids` | `list(string)` | yes | — | Lambda ENI subnets (≥2 AZs). |
| `queue_key` | `string` | no | `arq:queue` | Valkey list key. |
| `schedule_expression` | `string` | no | `rate(1 minute)` | EventBridge cadence. |
| `metric_namespace` | `string` | no | `Connect/ARQ` | Pass to `alarms.prometheus_namespace`. |
| `metric_name` | `string` | no | `connect_arq_queue_depth` | |
| `log_retention_days` | `number` | no | `7` | |
| `tags` | `map(string)` | no | `{}` | |

## Outputs

| Name | Description |
|---|---|
| `lambda_function_arn` / `lambda_function_name` | Standard. |
| `lambda_security_group_id` | Reference from the Valkey SG ingress rule. |
| `metric_namespace` / `metric_name` | Pass to the worker ECS Service's TargetTracking policy. |

## Caller wiring (root composition)

The Lambda's SG egresses 6379 to `0.0.0.0/0`; **ingress to Valkey** is enforced on the Valkey SG. Add this rule at root:

```hcl
resource "aws_security_group_rule" "valkey_from_queue_metric" {
  type                     = "ingress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  security_group_id        = aws_security_group.valkey.id
  source_security_group_id = module.queue_metric.lambda_security_group_id
}
```
