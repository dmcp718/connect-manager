output "app_url" {
  description = "Application URL"
  value       = "https://${var.domain}"
}

output "alb_dns_name" {
  description = "ALB DNS name"
  value       = aws_lb.main.dns_name
}

output "deploy_bucket" {
  description = "S3 bucket for deployment artifacts"
  value       = aws_s3_bucket.deploy.id
}

output "efs_id" {
  description = "EFS filesystem ID"
  value       = aws_efs_file_system.data.id
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}
