# terraform/modules/ecs-cluster

ECS cluster + Fargate capacity providers (`FARGATE` + `FARGATE_SPOT`). Container Insights enabled by default — required for the `alarms` module's pod-level metrics.

## Inputs

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `cluster_name` | `string` | yes | — | Used as-is. Convention: `connect-<env>`. |
| `container_insights` | `bool` | no | `true` | Container Insights toggle. |
| `tags` | `map(string)` | no | `{}` | Extra tags. |

## Outputs

| Name | Description |
|---|---|
| `cluster_id` | ECS cluster ID. |
| `cluster_arn` | ECS cluster ARN. |
| `cluster_name` | ECS cluster name. |

## Capacity-provider strategy

Per-service strategy is configured by the `ecs-service-*` modules, not here. The cluster-level *default* is `FARGATE` weight=1 base=1 so a service that doesn't override gets safe on-demand placement.
