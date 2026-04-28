locals {
  tags = merge({ project = "connect", env = var.name }, var.tags)

  # Use AWS-managed key when caller passes empty string.
  kms_key_id = var.kms_key_id != "" ? var.kms_key_id : null

  # replication_group_id is capped at 40 characters by the ElastiCache API.
  replication_group_id = substr("connect-${var.name}", 0, 40)
}

# ---------------------------------------------------------------------------
# Subnet group
# ---------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "this" {
  name        = "connect-${var.name}"
  description = "connect-${var.name} ElastiCache subnet group"
  subnet_ids  = var.subnet_ids

  tags = merge(local.tags, { Name = "connect-${var.name}" })
}

# ---------------------------------------------------------------------------
# Parameter group
# ---------------------------------------------------------------------------

resource "aws_elasticache_parameter_group" "this" {
  # "valkey8" family is supported by the AWS provider >= 5.26. If your provider
  # version predates that, upgrade to aws ~> 5.26 or later. There is no redis7
  # fallback — Valkey 8 and Redis 7 use incompatible parameter namespaces.
  name        = "connect-${var.name}-valkey8"
  family      = "valkey8"
  description = "connect-${var.name} Valkey 8 parameter group"

  tags = merge(local.tags, { Name = "connect-${var.name}-valkey8" })
}

# ---------------------------------------------------------------------------
# AUTH token — letters + digits only; Valkey rejects many special characters
# in AUTH tokens when TLS is enforced.
# ---------------------------------------------------------------------------

resource "random_password" "this" {
  length  = 32
  special = false
}

# ---------------------------------------------------------------------------
# Secrets Manager — Valkey connection bundle
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "valkey" {
  name        = "/connect/${var.name}/valkey"
  description = "connect-${var.name} ElastiCache Valkey AUTH token and connection details"
  kms_key_id  = local.kms_key_id

  tags = merge(local.tags, { Name = "/connect/${var.name}/valkey" })
}

resource "aws_secretsmanager_secret_version" "valkey" {
  secret_id = aws_secretsmanager_secret.valkey.id

  secret_string = jsonencode({
    host       = aws_elasticache_replication_group.this.primary_endpoint_address
    port       = 6379
    auth_token = random_password.this.result
    # rediss:// (double-s) signals TLS to redis-py / aioredis / ARQ.
    url = "rediss://default:${random_password.this.result}@${aws_elasticache_replication_group.this.primary_endpoint_address}:6379"
  })

  # Ensure the replication group is fully available before writing the live
  # endpoint into the secret — primary_endpoint_address is only populated
  # after the group reaches the "available" state.
  depends_on = [aws_elasticache_replication_group.this]
}

# ---------------------------------------------------------------------------
# ElastiCache replication group — single-node Valkey 8 with TLS + AUTH
# ---------------------------------------------------------------------------

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = local.replication_group_id
  description          = "Connect Manager Valkey for ARQ + activity stream"

  engine         = "valkey"
  engine_version = var.engine_version

  node_type          = var.node_type
  num_cache_clusters = 1

  # Single primary — failover requires >= 2 nodes.
  automatic_failover_enabled = false

  subnet_group_name    = aws_elasticache_subnet_group.this.name
  security_group_ids   = var.security_group_ids
  parameter_group_name = aws_elasticache_parameter_group.this.name
  port                 = 6379

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.this.result

  # ROTATE lets AWS re-encrypt in-place without a replacement; avoids the
  # destructive SET strategy that tears down and recreates the group.
  auth_token_update_strategy = "ROTATE"

  apply_immediately        = false
  snapshot_retention_limit = 1

  tags = merge(local.tags, { Name = local.replication_group_id })
}
