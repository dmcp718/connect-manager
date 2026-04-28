output "repository_uris" {
  description = "Map of repository name → repository URI (e.g. 123456789012.dkr.ecr.us-east-1.amazonaws.com/connect-web)."
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "repository_arns" {
  description = "Map of repository name → repository ARN."
  value       = { for k, r in aws_ecr_repository.this : k => r.arn }
}

output "repository_names" {
  description = "List of created ECR repository names (passthrough of var.repo_names, but reflects the resource keys for stable iteration)."
  value       = sort([for k, _ in aws_ecr_repository.this : k])
}
