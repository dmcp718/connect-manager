output "vpc_id" {
  description = "ID of the VPC."
  value       = module.vpc.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs (one per AZ)."
  value       = module.vpc.private_subnet_ids
}

output "ecr_repository_uris" {
  description = "Map of ECR repo name → repository URI. Consumed by the CI publish workflow and ECS task definitions."
  value       = module.ecr.repository_uris
}

output "ecr_repository_arns" {
  description = "Map of ECR repo name → repository ARN. Used by IAM policy that grants pull/push permissions."
  value       = module.ecr.repository_arns
}

output "secrets_kms_key_arn" {
  description = "ARN of the customer-managed KMS key encrypting all connect secrets (alias/connect-secrets)."
  value       = module.secrets.kms_key_arn
}

output "module_secret_arns" {
  description = "Map of purpose → Secrets Manager ARN for module-owned secrets (jwt, admin)."
  value       = module.secrets.secret_arns
}

output "acm_cert_arn" {
  description = "ARN of the validated ACM certificate for var.domain."
  value       = module.acm_route53.acm_cert_arn
}

output "github_actions_role_arn" {
  description = "ARN of the GitHub Actions deploy role — add to GitHub repo secrets as AWS_ROLE_ARN. null when var.github_repo is empty."
  value       = var.github_repo == "" ? null : module.github_oidc[0].role_arn
}

# ─── Pending — populated once the ECS modules land ────────────────────────────
#
# - cluster_name (ECS cluster)
# - alb_dns_name
# - rds_endpoint, rds_master_secret_arn
# - elasticache_endpoint, elasticache_auth_secret_arn
# - task_role_arns (web, worker, lucidlink_api)
# - alarms_sns_topic_arn
