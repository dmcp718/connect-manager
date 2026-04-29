terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # >= 5.26 for the elasticache valkey8 parameter-group family.
      version = "~> 5.26"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Remote state — operator populates terraform/backend.tf out of band.
  # backend "s3" {
  #   bucket         = "your-terraform-state-bucket"
  #   key            = "lucidlink-connect/aws-fargate/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "terraform-locks"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project    = "connect"
      env        = var.env
      managed_by = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  cluster_name = "connect-${var.env}"

  tags = {
    project    = "connect"
    env        = var.env
    managed_by = "terraform"
  }

  web_image    = "${module.ecr.repository_uris["connect-web"]}:${var.web_image_tag}"
  worker_image = "${module.ecr.repository_uris["connect-worker"]}:${var.worker_image_tag}"

  # Plain (non-secret) env vars shared by web + worker + migrate.
  # DATABASE_URL_INSECURE_SSL=1 keeps TLS-in-transit (RDS parameter group
  # has rds.force_ssl=1) but skips X.509 chain validation, since the AWS
  # RDS CA bundle isn't baked into the image. The network path is private
  # (VPC-internal), so this is acceptable for this stack. Set to 0 (or
  # remove) once the RDS CA is shipped via Dockerfile.
  app_env_vars = merge(
    {
      ENV                       = var.env
      AWS_REGION                = var.aws_region
      LOG_LEVEL                 = var.log_level
      DATABASE_URL_INSECURE_SSL = "1"
      # Triggers app/services/secrets.py container_mode → Fernet-encrypted
      # file at $DATA_DIR/secrets.enc. Without it the module imports the
      # `keyring` package and tries to use the OS keychain, which doesn't
      # exist in a Fargate container — every "Load filespaces" / per-user
      # token write 500s with NoKeyringError. Per-task-instance state
      # (NOT multi-replica safe); follow-up: move per-user tokens to
      # Postgres-Fernet alongside the datastore creds.
      DATA_DIR = "/tmp/connect-secrets"
    },
    var.app_env_vars,
  )

  # Secret env vars: env-var name -> Secrets Manager valueFrom.
  # Format: <secret-arn>:<json-key>:<version-stage>:<version-id>. All three
  # trailing segments must be present even when empty; trimming any pair
  # makes ECS reject the ARN with "unexpected ARN format with parameters".
  # The bootstrap TUI seeds /connect/<env>/jwt as {"value":"<hex>"}, so we
  # extract the `value` key.
  app_secret_env_vars = {
    DATABASE_URL   = "${module.rds.master_secret_arn}:url::"
    VALKEY_URL     = "${module.elasticache.auth_secret_arn}:url::"
    JWT_SECRET_KEY = "${module.secrets.secret_arns["jwt"]}:value::"
    # Bootstrap admin user — seeded by TUI Step 5 / operator into the
    # /connect/<env>/admin secret as {"email": "...", "password": "..."}.
    # Without this wiring, ensure_admin_exists() falls back to the
    # admin@localhost / admin defaults baked into app/services/auth.py.
    ADMIN_EMAIL    = "${module.secrets.secret_arns["admin"]}:email::"
    ADMIN_PASSWORD = "${module.secrets.secret_arns["admin"]}:password::"
  }

  # All secret ARNs the Task Execution Role is allowed to read.
  all_secret_arns = concat(
    [module.rds.master_secret_arn, module.elasticache.auth_secret_arn],
    values(module.secrets.secret_arns),
  )
}

# ─────────────────────────────────────────────────────────────────────────────
# Network
# ─────────────────────────────────────────────────────────────────────────────

module "vpc" {
  source = "./modules/vpc"

  name = var.env
  cidr = var.vpc_cidr
  tags = local.tags
}

# ─────────────────────────────────────────────────────────────────────────────
# Secrets / KMS
# ─────────────────────────────────────────────────────────────────────────────
#
# eso_role_arn = account-root keeps the KMS key policy permissive at the key
# layer; the Task Execution Role's IAM policy (in module.task_iam) is the
# real gate on who can Decrypt with this CMK.

module "secrets" {
  source = "./modules/secrets"

  name_prefix  = "/connect/${var.env}"
  eso_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
  tags         = local.tags
}

# ─────────────────────────────────────────────────────────────────────────────
# ECR + ACM + ECS cluster
# ─────────────────────────────────────────────────────────────────────────────

module "ecr" {
  source = "./modules/ecr"

  repo_names = var.ecr_repo_names
  tags       = local.tags
}

module "acm_route53" {
  source = "./modules/acm-route53"

  domain  = var.domain
  zone_id = var.route53_zone_id
  tags    = local.tags
}

module "ecs_cluster" {
  source = "./modules/ecs-cluster"

  cluster_name = local.cluster_name
  tags         = local.tags
}

# ─────────────────────────────────────────────────────────────────────────────
# ALB
# ─────────────────────────────────────────────────────────────────────────────

module "alb" {
  source = "./modules/alb"

  name              = var.env
  vpc_id            = module.vpc.vpc_id
  public_subnet_ids = module.vpc.public_subnet_ids
  acm_cert_arn      = module.acm_route53.acm_cert_arn
  ingress_cidrs     = var.alb_ingress_cidrs
  tags              = local.tags
}

resource "aws_route53_record" "app" {
  zone_id = var.route53_zone_id
  name    = var.domain
  type    = "A"

  alias {
    name                   = module.alb.alb_dns_name
    zone_id                = module.alb.alb_zone_id
    evaluate_target_health = true
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Data plane SGs (caller-owned per rds/elasticache module contract)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_security_group" "web_tasks" {
  name        = "connect-${var.env}-web-tasks"
  description = "Web Fargate tasks. Ingress from ALB on var.web_target_port; full egress."
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "HTTP from ALB"
    from_port       = var.web_target_port
    to_port         = var.web_target_port
    protocol        = "tcp"
    security_groups = [module.alb.alb_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "connect-${var.env}-web-tasks" })
}

resource "aws_security_group" "worker_tasks" {
  name        = "connect-${var.env}-worker-tasks"
  description = "Worker Fargate tasks. Egress only (no ingress)."
  vpc_id      = module.vpc.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "connect-${var.env}-worker-tasks" })
}

resource "aws_security_group" "rds" {
  name        = "connect-${var.env}-rds"
  description = "Postgres 5432 from web + worker tasks only."
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Postgres from web tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.web_tasks.id]
  }

  ingress {
    description     = "Postgres from worker tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.worker_tasks.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "connect-${var.env}-rds" })
}

resource "aws_security_group" "valkey" {
  name        = "connect-${var.env}-valkey"
  description = "Valkey 6379 (TLS) from web + worker tasks. Queue-metric Lambda ingress added separately as a security_group_rule."
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Valkey from web tasks"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.web_tasks.id]
  }

  ingress {
    description     = "Valkey from worker tasks"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.worker_tasks.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "connect-${var.env}-valkey" })
}

# Queue-metric Lambda → Valkey ingress (added as a separate rule so we can
# reference module.queue_metric.lambda_security_group_id without a circular
# dep on module.queue_metric inside aws_security_group.valkey).
resource "aws_security_group_rule" "valkey_from_queue_metric" {
  description              = "Valkey from queue-metric Lambda"
  type                     = "ingress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  security_group_id        = aws_security_group.valkey.id
  source_security_group_id = module.queue_metric.lambda_security_group_id
}

# ─────────────────────────────────────────────────────────────────────────────
# Data plane: RDS + ElastiCache
# ─────────────────────────────────────────────────────────────────────────────

module "rds" {
  source = "./modules/rds"

  name                = var.env
  subnet_ids          = module.vpc.private_subnet_ids
  security_group_ids  = [aws_security_group.rds.id]
  instance_class      = var.rds_instance_class
  multi_az            = var.rds_multi_az
  kms_key_id          = module.secrets.kms_key_arn
  deletion_protection = var.rds_deletion_protection
  skip_final_snapshot = var.rds_skip_final_snapshot
  tags                = local.tags
}

module "elasticache" {
  source = "./modules/elasticache"

  name               = var.env
  subnet_ids         = module.vpc.private_subnet_ids
  security_group_ids = [aws_security_group.valkey.id]
  node_type          = var.elasticache_node_type
  kms_key_id         = module.secrets.kms_key_arn
  tags               = local.tags
}

# ─────────────────────────────────────────────────────────────────────────────
# Task IAM (depends on module.ecr + module.rds + module.elasticache + module.secrets)
# ─────────────────────────────────────────────────────────────────────────────

module "task_iam" {
  source = "./modules/task-iam"

  env                 = var.env
  aws_region          = var.aws_region
  ecr_repository_arns = values(module.ecr.repository_arns)
  secret_arns         = local.all_secret_arns
  kms_key_arn         = module.secrets.kms_key_arn
  tags                = local.tags
}

# ─────────────────────────────────────────────────────────────────────────────
# Queue-metric Lambda (publishes ARQ queue depth → CloudWatch)
# ─────────────────────────────────────────────────────────────────────────────

module "queue_metric" {
  source = "./modules/queue-metric"

  env                = var.env
  aws_region         = var.aws_region
  valkey_secret_arn  = module.elasticache.auth_secret_arn
  kms_key_arn        = module.secrets.kms_key_arn
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  tags               = local.tags
}

# ─────────────────────────────────────────────────────────────────────────────
# ECS services
# ─────────────────────────────────────────────────────────────────────────────

module "ecs_service_web" {
  source = "./modules/ecs-service-web"

  env                = var.env
  aws_region         = var.aws_region
  cluster_arn        = module.ecs_cluster.cluster_arn
  execution_role_arn = module.task_iam.execution_role_arn
  task_role_arn      = module.task_iam.role_arns.web
  subnet_ids         = module.vpc.private_subnet_ids
  security_group_ids = [aws_security_group.web_tasks.id]
  target_group_arn   = module.alb.web_target_group_arn
  web_image          = local.web_image
  # Mirror of upstream lucidlink/lucidlink-api:latest in our ECR. We can't
  # rely on Docker Hub directly: anonymous pulls hit the 200/6h rate
  # limit after a few back-to-back deploys and ECS gets stuck in
  # CannotPullContainerError. The mirror is created/updated by the
  # operator with `docker buildx imagetools create -t <ecr>:<tag>
  # lucidlink/lucidlink-api:<tag>`.
  lucidlink_api_image = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/lucidlink-api:latest"
  web_port            = var.web_target_port

  desired_count = var.web_desired_count
  min_count     = var.web_min_count
  max_count     = var.web_max_count

  env_vars = merge(local.app_env_vars, {
    LL_API_HOST = "http://localhost:${var.lucidlink_api_port}/api/v1"
  })
  secret_env_vars = local.app_secret_env_vars

  tags = local.tags
}

module "ecs_service_worker" {
  source = "./modules/ecs-service-worker"

  env                = var.env
  aws_region         = var.aws_region
  cluster_arn        = module.ecs_cluster.cluster_arn
  execution_role_arn = module.task_iam.execution_role_arn
  task_role_arn      = module.task_iam.role_arns.worker
  subnet_ids         = module.vpc.private_subnet_ids
  security_group_ids = [aws_security_group.worker_tasks.id]
  worker_image       = local.worker_image

  metric_namespace              = module.queue_metric.metric_namespace
  metric_name                   = module.queue_metric.metric_name
  target_queue_depth_per_worker = var.worker_target_queue_depth

  desired_count = var.worker_desired_count
  min_count     = var.worker_min_count
  max_count     = var.worker_max_count

  env_vars        = local.app_env_vars
  secret_env_vars = local.app_secret_env_vars

  tags = local.tags
}

module "ecs_task_migrate" {
  source = "./modules/ecs-task-migrate"

  env                = var.env
  aws_region         = var.aws_region
  execution_role_arn = module.task_iam.execution_role_arn
  task_role_arn      = module.task_iam.role_arns.web
  web_image          = local.web_image

  env_vars = local.app_env_vars
  secret_env_vars = {
    DATABASE_URL = local.app_secret_env_vars.DATABASE_URL
  }

  tags = local.tags
}

# ─────────────────────────────────────────────────────────────────────────────
# Alarms
# ─────────────────────────────────────────────────────────────────────────────

module "alarms" {
  source = "./modules/alarms"

  cluster_name                 = module.ecs_cluster.cluster_name
  db_instance_id               = module.rds.db_instance_id
  cache_cluster_id             = module.elasticache.replication_group_id
  alb_arn_suffix               = module.alb.alb_arn_suffix
  prometheus_namespace         = module.queue_metric.metric_namespace
  sns_topic_subscription_email = var.alarms_email

  tags = local.tags
}

# ─────────────────────────────────────────────────────────────────────────────
# GitHub OIDC deploy role (gated on var.github_repo)
# ─────────────────────────────────────────────────────────────────────────────

module "github_oidc" {
  count  = var.github_repo == "" ? 0 : 1
  source = "./modules/github-oidc"

  github_repo = var.github_repo
  cluster_arn = module.ecs_cluster.cluster_arn
  task_role_arns = concat(
    [module.task_iam.execution_role_arn],
    values(module.task_iam.role_arns),
  )
  tags = local.tags
}
