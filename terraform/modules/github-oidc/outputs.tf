output "role_arn" {
  description = "ARN of the connect-github-actions IAM role — add to GitHub repo secrets as AWS_ROLE_ARN."
  value       = aws_iam_role.github_actions.arn
}

output "role_name" {
  description = "Name of the connect-github-actions IAM role. Use to attach additional policies at root if needed."
  value       = aws_iam_role.github_actions.name
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub Actions OIDC provider."
  value       = aws_iam_openid_connect_provider.github_actions.arn
}
