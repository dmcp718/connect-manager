# terraform/modules/ecs-task-migrate

One-shot ECS Task Definition that runs `alembic upgrade head`. Replaces the Helm pre-install/pre-upgrade Job from the K8s plan.

## Why a separate task def

Could be done with a `command` override on the web task def at `run-task` time. Having a dedicated definition is cleaner:

- Explicit `*-migrate` family in `aws ecs list-task-definitions` makes ops obvious.
- Different log group (`/aws/ecs/connect-<env>/migrate`) so deploy logs don't pollute web logs.
- Independent CPU/memory sizing (defaults 0.25 vCPU / 512 MiB — migrations are fast and small).
- Hard to accidentally `update-service` against it.

## CI usage

```bash
aws ecs run-task \
  --cluster $CLUSTER_NAME \
  --task-definition connect-prod-migrate \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_IDS],securityGroups=[$WEB_TASK_SG],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' --output text \
  | xargs -I{} aws ecs wait tasks-stopped --cluster $CLUSTER_NAME --tasks {}
```

Then check the exit code:

```bash
aws ecs describe-tasks --cluster $CLUSTER_NAME --tasks $TASK_ARN \
  --query 'tasks[0].containers[0].exitCode'
```

Non-zero → fail the deploy and investigate. The CD workflow gates the `update-service` step on this returning 0.

## Image and entrypoint expectations

The Dockerfile copied alembic.ini + alembic/ to `/migrations`. The default `command` is `alembic -c /migrations/alembic.ini upgrade head`. Override `var.command` for downgrades or other ops.

`secret_env_vars` must include `DATABASE_URL` so Alembic can connect.

## Inputs / Outputs

See `variables.tf` and `outputs.tf`.
