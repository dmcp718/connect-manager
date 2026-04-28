locals {
  tags = merge({ project = "connect", env = var.name }, var.tags)

  # Use AWS-managed key when caller passes empty string.
  kms_key_id = var.kms_key_id != "" ? var.kms_key_id : null

  # Strip the port suffix that RDS appends to the endpoint attribute (host:port).
  db_host = split(":", aws_db_instance.this.endpoint)[0]
}

# ---------------------------------------------------------------------------
# Subnet group
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "this" {
  name        = "connect-${var.name}"
  description = "connect-${var.name} RDS subnet group"
  subnet_ids  = var.subnet_ids

  tags = merge(local.tags, { Name = "connect-${var.name}" })
}

# ---------------------------------------------------------------------------
# Parameter group — TLS required
# ---------------------------------------------------------------------------

resource "aws_db_parameter_group" "this" {
  name        = "connect-${var.name}-pg16"
  family      = "postgres16"
  description = "connect-${var.name} Postgres 16 -- TLS required"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  tags = merge(local.tags, { Name = "connect-${var.name}-pg16" })
}

# ---------------------------------------------------------------------------
# Master password
# ---------------------------------------------------------------------------

resource "random_password" "master" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

# ---------------------------------------------------------------------------
# Secrets Manager — master credentials
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "master" {
  name        = "/connect/${var.name}/db"
  description = "connect-${var.name} RDS master credentials"
  kms_key_id  = local.kms_key_id

  tags = merge(local.tags, { Name = "/connect/${var.name}/db" })
}

resource "aws_secretsmanager_secret_version" "master" {
  secret_id = aws_secretsmanager_secret.master.id

  secret_string = jsonencode({
    username = var.username
    password = random_password.master.result
    host     = local.db_host
    port     = 5432
    dbname   = var.db_name
    url      = "postgresql+asyncpg://${var.username}:${random_password.master.result}@${local.db_host}:5432/${var.db_name}"
  })

  # Ensure the DB instance is fully created before we write the live endpoint
  # into the secret version.
  depends_on = [aws_db_instance.this]
}

# ---------------------------------------------------------------------------
# RDS instance
# ---------------------------------------------------------------------------

resource "aws_db_instance" "this" {
  identifier = "connect-${var.name}"

  engine         = "postgres"
  engine_version = var.engine_version

  instance_class    = var.instance_class
  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.db_name
  username = var.username
  password = random_password.master.result

  vpc_security_group_ids = var.security_group_ids
  db_subnet_group_name   = aws_db_subnet_group.this.name
  parameter_group_name   = aws_db_parameter_group.this.name

  multi_az = var.multi_az

  backup_retention_period = 7
  backup_window           = "04:00-05:00"
  maintenance_window      = "Sun:05:00-Sun:06:00"

  skip_final_snapshot       = false
  final_snapshot_identifier = "connect-${var.name}-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"

  deletion_protection = true

  performance_insights_enabled          = true
  performance_insights_retention_period = 7

  enabled_cloudwatch_logs_exports = ["postgresql"]

  tags = merge(local.tags, { Name = "connect-${var.name}" })

  lifecycle {
    # formatdate(timestamp()) changes on every plan; ignore to avoid perpetual drift.
    ignore_changes = [final_snapshot_identifier]
  }
}
