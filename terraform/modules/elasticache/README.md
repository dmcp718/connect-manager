# terraform/modules/elasticache

Self-contained ElastiCache **Valkey 8** replication group with TLS in transit, AUTH token, and connection bundle seeded into Secrets Manager.

## Engine family note

This module uses `family = "valkey8"`, which requires the AWS Terraform provider **>= 5.26**. The `versions.tf` constraint (`~> 5.0`) permits it. There is no `redis7` fallback — Valkey 8 and Redis 7 parameter groups use incompatible namespaces. If you run against a provider older than 5.26 and see `InvalidParameterValueException: Invalid parameter group family valkey8`, upgrade the provider.

## AUTH token

`random_password` is generated with `special = false` (letters + digits only). ElastiCache rejects many special characters (e.g. `@`, `:`, `/`) in AUTH tokens when `transit_encryption_enabled = true` because they collide with the `rediss://` URL grammar.

## Secret schema

The Secrets Manager secret at `/connect/<name>/valkey` contains:

```json
{
  "host": "<primary_endpoint_address>",
  "port": 6379,
  "auth_token": "<32-char alphanumeric token>",
  "url": "rediss://default:<token>@<host>:6379"
}
```

`rediss://` (double-s) signals TLS to redis-py, aioredis, and ARQ's built-in Valkey/Redis client.

## Consumption

- **ESO ExternalSecret** (Epic 3.7 / awsk-rp3.7): mounts `auth_secret_arn` as a K8s Secret in the `connect` namespace.
- **KEDA ScaledObject** (awsk-t28.6): reads `VALKEY_ADDRESS` (derived from the secret's `url` field) to drive worker autoscaling based on ARQ queue depth.
- **ARQ worker pods**: consume `VALKEY_URL` env var injected from the ESO-managed K8s Secret.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `name` | `string` | required | Deployment name (used in resource names and Secrets Manager path) |
| `subnet_ids` | `list(string)` | required | Private subnet IDs for the ElastiCache subnet group |
| `security_group_ids` | `list(string)` | required | Security group IDs attached to the replication group |
| `node_type` | `string` | `"cache.t4g.micro"` | ElastiCache node type |
| `engine_version` | `string` | `"8.0"` | Valkey engine version |
| `kms_key_id` | `string` | `""` | KMS key ARN for Secrets Manager encryption (empty = AWS-managed key) |
| `tags` | `map(string)` | `{}` | Additional tags merged onto every taggable resource |

## Outputs

| Name | Description |
|---|---|
| `primary_endpoint_address` | Primary endpoint hostname |
| `port` | Port (always 6379) |
| `auth_secret_arn` | ARN of the Secrets Manager secret |
| `auth_secret_name` | Name of the Secrets Manager secret |
| `parameter_group_name` | Name of the ElastiCache parameter group |
