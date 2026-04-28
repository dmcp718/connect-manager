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
}

# ── VPC ──────────────────────────────────────────────────────────────────────

module "vpc" {
  source = "./modules/vpc"

  name = var.env
  cidr = var.vpc_cidr
  tags = local.tags
}

# ── Secrets Manager + customer-managed KMS ───────────────────────────────────
#
# eso_role_arn is currently the AWS account root, which permits the future
# task role to be granted decrypt rights via the KMS key policy. When the
# task-role IAM module lands, replace with module.task_iam.role_arns.app.

module "secrets" {
  source = "./modules/secrets"

  name_prefix  = "/connect/${var.env}"
  eso_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
  tags         = local.tags
}

# ── ECR ──────────────────────────────────────────────────────────────────────

module "ecr" {
  source = "./modules/ecr"

  repo_names = var.ecr_repo_names
  tags       = local.tags
}

# ── ACM cert + Route53 validation records ────────────────────────────────────

module "acm_route53" {
  source = "./modules/acm-route53"

  domain  = var.domain
  zone_id = var.route53_zone_id
  tags    = local.tags
}

# ── GitHub Actions OIDC + deploy role (gated on var.github_repo) ─────────────
#
# CI host = GitHub Actions on dmcp718/connect-manager. The deploy role's
# trust policy is scoped inside the module to refs/heads/aws-fargate +
# refs/tags/v*.

module "github_oidc" {
  count  = var.github_repo == "" ? 0 : 1
  source = "./modules/github-oidc"

  github_repo     = var.github_repo
  eks_cluster_arn = "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:cluster/${local.cluster_name}"
  tags            = local.tags
}

# ─────────────────────────────────────────────────────────────────────────────
# TODO: ECS-specific modules (next commit)
# ─────────────────────────────────────────────────────────────────────────────
#
# Pending Fargate-specific modules:
#
#   - terraform/modules/task-iam        — Task Execution Role + per-service
#                                         Task Roles (replaces Pod Identity IAM).
#   - terraform/modules/ecs-cluster     — ECS cluster + capacity providers
#                                         (FARGATE + FARGATE_SPOT).
#   - terraform/modules/alb             — ALB + listeners + target groups.
#   - terraform/modules/ecs-service-web — Web Task Definition (web container +
#                                         lucidlink-api sidecar) + Service +
#                                         Auto Scaling target.
#   - terraform/modules/ecs-service-worker — Worker Task Definition + Service +
#                                         Auto Scaling target with custom
#                                         metric (ARQ queue depth → CloudWatch).
#
# Once those land, this file will:
#
#   1. Define security groups: alb (public 443), web-tasks (from ALB only),
#      worker-tasks (egress only), rds (from web/worker), valkey (from web/
#      worker).
#   2. module "task_iam"      with cluster_name = local.cluster_name
#   3. module "ecs_cluster"   with cluster_name = local.cluster_name
#   4. module "alb"           with vpc + acm_cert_arn = module.acm_route53.acm_cert_arn
#   5. module "ecs_service_web"    consuming ECR repo URI + secret ARNs
#   6. module "ecs_service_worker" consuming the same
#   7. module "rds"           with security_group_ids = [aws_security_group.rds.id]
#   8. module "elasticache"   with security_group_ids = [aws_security_group.valkey.id]
#   9. module "alarms"        with cluster_name = ECS cluster name +
#                             db_instance_id + replication_group_id +
#                             alb_arn_suffix from the new alb module.
