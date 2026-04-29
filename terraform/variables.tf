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
  description = "Fully qualified domain for the application (e.g. connect.example.com). The ACM cert covers this and *.<domain>; Route53 ALIAS at the apex points to the ALB."
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
  description = "ECR repository names to provision."
  type        = list(string)
  default     = ["connect-web", "connect-worker"]
}

variable "github_repo" {
  description = "GitHub repository in org/repo format (e.g. dmcp718/connect-manager). Empty disables the github-oidc module — useful for ministack runs."
  type        = string
  default     = ""
}

# ─── ALB ──────────────────────────────────────────────────────────────────────

variable "alb_ingress_cidrs" {
  description = "CIDR blocks allowed to reach the ALB. Default 0.0.0.0/0 = public."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "web_target_port" {
  description = "Port the web container listens on. Used as both the ALB target group port and the ingress rule on the web-tasks SG."
  type        = number
  default     = 8000
}

variable "lucidlink_api_port" {
  description = "Port the lucidlink-api sidecar listens on (web reaches it at http://localhost:<port>/api/v1)."
  type        = number
  default     = 3003
}

# ─── Web service ──────────────────────────────────────────────────────────────

variable "web_image_tag" {
  description = "Tag of the connect-web image to run. CI updates the running task definition out of band; this is the default for fresh applies."
  type        = string
  default     = "latest"
}

variable "web_desired_count" {
  description = "Initial / steady-state web task count. Auto Scaling owns it after creation."
  type        = number
  default     = 2
}

variable "web_min_count" {
  description = "Minimum web task count under Auto Scaling."
  type        = number
  default     = 2
}

variable "web_max_count" {
  description = "Maximum web task count under Auto Scaling."
  type        = number
  default     = 6
}

# ─── Worker service ───────────────────────────────────────────────────────────

variable "worker_image_tag" {
  description = "Tag of the connect-worker image to run."
  type        = string
  default     = "latest"
}

variable "worker_desired_count" {
  description = "Initial worker task count. Default 0 — Auto Scaling will spin up tasks as queue depth demands."
  type        = number
  default     = 0
}

variable "worker_min_count" {
  description = "Minimum worker task count. Default 0 = scale-to-zero."
  type        = number
  default     = 0
}

variable "worker_max_count" {
  description = "Maximum worker task count under Auto Scaling."
  type        = number
  default     = 8
}

variable "worker_target_queue_depth" {
  description = "Target tracking value: average ARQ queue depth per running worker. ECS scales up when actual depth/N exceeds this."
  type        = number
  default     = 5
}

# ─── Data plane ───────────────────────────────────────────────────────────────

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

variable "rds_deletion_protection" {
  description = "RDS deletion protection. Default true (production-style). Smokes (one-shot deploys planning to terraform destroy) MUST set false in tfvars or destroy will fail."
  type        = bool
  default     = true
}

variable "rds_skip_final_snapshot" {
  description = "Skip the RDS final snapshot on delete. Default false (keep snapshot, production-style). Smokes set true to avoid the artifact + the few minutes the snapshot takes."
  type        = bool
  default     = false
}

variable "elasticache_node_type" {
  description = "ElastiCache node type for the Valkey replication group."
  type        = string
  default     = "cache.t4g.micro"
}

# ─── Observability ────────────────────────────────────────────────────────────

variable "alarms_email" {
  description = "Optional email address subscribed to the connect-alerts SNS topic."
  type        = string
  default     = ""
}

variable "log_level" {
  description = "Application LOG_LEVEL env var (DEBUG/INFO/WARNING/ERROR)."
  type        = string
  default     = "INFO"
}

# ─── App env passthrough ──────────────────────────────────────────────────────

variable "app_env_vars" {
  description = "Extra non-secret environment variables passed to web + worker + migrate. Merged on top of the base set (ENV, AWS_REGION, LOG_LEVEL)."
  type        = map(string)
  default     = {}
}
