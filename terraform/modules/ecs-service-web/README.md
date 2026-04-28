# terraform/modules/ecs-service-web

Fargate web service: 2-container Task Definition (FastAPI + lucidlink-api sidecar), Service attached to the ALB target group, target-tracking CPU Auto Scaling.

## Container layout

| Container | Image | Port | Notes |
|---|---|---|---|
| `lucidlink-api` (sidecar) | `var.lucidlink_api_image` | 3003 | TCP healthcheck. Web reaches it at `http://localhost:3003/api/v1`. Started first (web depends on it being `HEALTHY`). |
| `web` | `var.web_image` (your ECR URI) | 8000 | The FastAPI app. Receives `var.env_vars` plain + `var.secret_env_vars` via Secrets Manager `valueFrom`. |

Both containers share the task ENI (`awsvpc`), so localhost networking is available between them.

## Secrets

`secret_env_vars` is a `map(string)` from env-var name to a Secrets Manager `valueFrom` string. Use the Secrets Manager `<secret-arn>:<json-key>:<version-stage>:<version-id>` syntax to extract a single JSON field:

```hcl
secret_env_vars = {
  DATABASE_URL = "${module.rds.master_secret_arn}:url::"
  VALKEY_URL   = "${module.elasticache.auth_secret_arn}:url::"
  JWT_SECRET   = "${module.secrets.secret_arns["jwt"]}:value::"
}
```

ECS resolves these at task-start time using the Task Execution Role's `secretsmanager:GetSecretValue`.

## Auto Scaling

Single target-tracking policy on `ECSServiceAverageCPUUtilization` at `var.target_cpu_utilization` (default 60%). `scale_in_cooldown=300` keeps replicas warm during traffic dips; `scale_out_cooldown=60` reacts quickly to spikes. Override via `var.min_count` / `var.max_count` (default 2 / 6).

The `desired_count` attribute on the service is `ignore_changes` — Auto Scaling owns it after the initial create.

## Capacity provider

Default `FARGATE` (on-demand). Setting `var.use_fargate_spot=true` switches to `FARGATE_SPOT`; **not recommended for web** because mid-request task termination is user-visible. Acceptable for staging.

## Inputs

See `variables.tf` — every knob is documented inline.

## Outputs

| Name | Description |
|---|---|
| `service_name` | Pass to `aws ecs update-service --service`. |
| `service_arn` | |
| `task_definition_arn` | Snapshot at apply; CI registers new revisions out of band. |
| `task_definition_family` | |
