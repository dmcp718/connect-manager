# terraform/modules/task-iam

ECS Task Execution Role (one shared) + per-service Task Roles for web, worker, and the lucidlink-api sidecar. Replaces the Pod-Identity IAM module from `aws-kubernetes`.

## Roles

| Role | Purpose |
|---|---|
| `connect-<env>-task-execution` | Attached as `executionRoleArn` on every Task Definition. ECR pull on `connect-*` repos, Secrets Manager read on the configured ARN list, KMS Decrypt on the connect-secrets CMK, log-stream create/append on `/aws/ecs/connect-<env>/*`. |
| `connect-<env>-task-web` | `taskRoleArn` for the web service. CloudWatch `PutMetricData` scoped to `Connect/Web` + `Connect/ARQ` namespaces. |
| `connect-<env>-task-worker` | `taskRoleArn` for the worker service. Same metric scope as web. |
| `connect-<env>-task-lucidlink-api` | `taskRoleArn` for the lucidlink-api sidecar. No AWS-side permissions today; exists so future grants don't need a task-definition rewrite. |

Trust policy for all roles: `ecs-tasks.amazonaws.com` with `aws:SourceArn` + `aws:SourceAccount` confused-deputy conditions.

## Inputs

| Name | Type | Required | Description |
|---|---|---|---|
| `env` | `string` | yes | Environment name (used in role names + log-group ARN scoping). |
| `aws_region` | `string` | yes | AWS region for log-group + source-arn ARN scoping. |
| `ecr_repository_arns` | `list(string)` | yes | ECR repo ARNs the execution role can pull from. Typically `values(module.ecr.repository_arns)`. |
| `secret_arns` | `list(string)` | yes | Secrets Manager secret ARNs the execution role can read. |
| `kms_key_arn` | `string` | yes | ARN of the connect-secrets KMS key. |
| `tags` | `map(string)` | no | Extra tags. |

## Outputs

| Name | Description |
|---|---|
| `execution_role_arn` | ARN of the shared Task Execution Role. |
| `role_arns` | Map keyed by `web`/`worker`/`lucidlink_api` → ARN of each Task Role. |

## Customer-credential boundary

App-owned AWS calls use the Task Role default credential chain via `app/services/aws.py`. Customer-owned credentials (per-datastore S3 / SQS keys decrypted from Postgres) flow through `s3_service` / `sqs_service` and use STS, not the Task Role — so they are deliberately *not* granted here.
