# ── Network ──────────────────────────────────────────────────────────────────

output "vpc_id" {
  description = "ID of the VPC."
  value       = module.vpc.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs (one per AZ). Pass these to `aws ecs run-task` and to the bootstrap TUI's awsvpcConfiguration."
  value       = module.vpc.private_subnet_ids
}

# ── ECS ──────────────────────────────────────────────────────────────────────

output "cluster_name" {
  description = "ECS cluster name."
  value       = module.ecs_cluster.cluster_name
}

output "cluster_arn" {
  description = "ECS cluster ARN."
  value       = module.ecs_cluster.cluster_arn
}

output "web_service_name" {
  description = "Web ECS service name. Pass to `aws ecs update-service --service <name>`."
  value       = module.ecs_service_web.service_name
}

output "worker_service_name" {
  description = "Worker ECS service name."
  value       = module.ecs_service_worker.service_name
}

output "migrate_task_definition_family" {
  description = "Migration task definition family. Pass to `aws ecs run-task --task-definition <family>`."
  value       = module.ecs_task_migrate.task_definition_family
}

output "web_task_security_group_id" {
  description = "Web tasks SG ID. Use as the awsvpcConfiguration.securityGroups for `aws ecs run-task` (migrations and one-off jobs need DB access)."
  value       = aws_security_group.web_tasks.id
}

# ── ALB ──────────────────────────────────────────────────────────────────────

output "alb_dns_name" {
  description = "Public DNS name of the ALB. Route53 ALIAS at var.domain points here."
  value       = module.alb.alb_dns_name
}

output "app_url" {
  description = "Application URL."
  value       = "https://${var.domain}"
}

# ── ECR ──────────────────────────────────────────────────────────────────────

output "ecr_repository_uris" {
  description = "Map of ECR repo name → repository URI. Consumed by the CI publish workflow."
  value       = module.ecr.repository_uris
}

# ── Data plane ───────────────────────────────────────────────────────────────

output "rds_endpoint" {
  description = "RDS Postgres endpoint."
  value       = module.rds.endpoint
}

output "rds_master_secret_arn" {
  description = "Secrets Manager ARN holding RDS master credentials."
  value       = module.rds.master_secret_arn
}

output "elasticache_endpoint" {
  description = "ElastiCache Valkey primary endpoint."
  value       = module.elasticache.primary_endpoint_address
}

output "elasticache_auth_secret_arn" {
  description = "Secrets Manager ARN holding the Valkey AUTH token + connection URL."
  value       = module.elasticache.auth_secret_arn
}

# ── Secrets / KMS ────────────────────────────────────────────────────────────

output "secrets_kms_key_arn" {
  description = "ARN of the customer-managed KMS key encrypting all connect secrets."
  value       = module.secrets.kms_key_arn
}

output "module_secret_arns" {
  description = "Map of purpose → Secrets Manager ARN for module-owned secrets (jwt, admin)."
  value       = module.secrets.secret_arns
}

# ── IAM ──────────────────────────────────────────────────────────────────────

output "task_execution_role_arn" {
  description = "ECS Task Execution Role ARN."
  value       = module.task_iam.execution_role_arn
}

output "task_role_arns" {
  description = "Map of service key → Task Role ARN (web, worker, lucidlink_api)."
  value       = module.task_iam.role_arns
}

# ── Alarms ───────────────────────────────────────────────────────────────────

output "alarms_sns_topic_arn" {
  description = "SNS topic ARN for connect-alerts."
  value       = module.alarms.sns_topic_arn
}

# ── GitHub OIDC ──────────────────────────────────────────────────────────────

output "github_actions_role_arn" {
  description = "ARN of the GitHub Actions deploy role — add to repo secrets as AWS_ROLE_ARN. null when var.github_repo is empty."
  value       = var.github_repo == "" ? null : module.github_oidc[0].role_arn
}
