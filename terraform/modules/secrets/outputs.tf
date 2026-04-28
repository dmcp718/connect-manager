output "secret_arns" {
  description = <<-EOT
    Map of purpose → Secrets Manager secret ARN for module-owned secrets
    (`jwt`, `admin`). Consumed by ESO ExternalSecret resources in Epic 3.7.

    Note: the `db` and `valkey-auth` purposes are owned by the rds and
    elasticache modules respectively; their ARNs are emitted from those
    modules' `master_secret_arn` / `auth_secret_arn` outputs.
  EOT
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

output "secret_names" {
  description = "Map of purpose → Secrets Manager secret name (e.g. /connect/prod/jwt)."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.name }
}

output "kms_key_arn" {
  description = "ARN of the customer-managed KMS key encrypting all connect secrets. Pass this into the rds and elasticache modules' `kms_key_id` input so all connect secrets share a single key."
  value       = aws_kms_key.this.arn
}

output "kms_key_id" {
  description = "ID (UUID) of the customer-managed KMS key. Some downstream resources accept the ID rather than the ARN."
  value       = aws_kms_key.this.key_id
}

output "kms_key_alias" {
  description = "Alias of the customer-managed KMS key (`alias/connect-secrets`). Stable identifier suitable for logs and dashboards."
  value       = aws_kms_alias.this.name
}
