variable "env" {
  description = "Environment name. Used in resource names, tags, secret paths, and the cluster name (connect-<env>)."
  type        = string
  default     = "prod"
}

variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "domain" {
  description = "Fully qualified domain for the application (e.g. connect.example.com). The ACM cert covers this and *.<domain>."
  type        = string
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone ID owning var.domain. Operator must create + delegate the zone before apply."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC. Distinct from aws-deploy's 10.0.0.0/16 so the two stacks can eventually peer."
  type        = string
  default     = "10.20.0.0/16"
}

variable "ecr_repo_names" {
  description = "ECR repository names to provision. Default covers the two app images shipped by this branch."
  type        = list(string)
  default     = ["connect-web", "connect-worker"]
}

variable "github_repo" {
  description = "GitHub repository in org/repo format (e.g. dmcp718/connect-manager). Empty disables the github-oidc module — useful for ministack runs and operators who don't use GitHub Actions."
  type        = string
  default     = ""
}

# ─── Pending — used once the ECS modules land ─────────────────────────────────

variable "rds_instance_class" {
  description = "RDS instance class for the Postgres instance."
  type        = string
  default     = "db.t4g.micro"
}

variable "rds_multi_az" {
  description = "Whether to enable Multi-AZ for RDS. Flip to true for production tiers."
  type        = bool
  default     = false
}

variable "elasticache_node_type" {
  description = "ElastiCache node type for the Valkey replication group."
  type        = string
  default     = "cache.t4g.micro"
}

variable "alarms_email" {
  description = "Optional email address subscribed to the connect-alerts SNS topic. Empty = no email subscription."
  type        = string
  default     = ""
}
