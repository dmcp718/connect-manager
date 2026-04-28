output "primary_endpoint_address" {
  description = "Primary endpoint hostname for the ElastiCache replication group."
  value       = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "port" {
  description = "ElastiCache port (always 6379 for Valkey)."
  value       = 6379
}

output "auth_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the Valkey AUTH token and connection URL. Consumed by ESO ExternalSecret in Epic 3.7."
  value       = aws_secretsmanager_secret.valkey.arn
}

output "auth_secret_name" {
  description = "Name of the Secrets Manager secret holding the Valkey AUTH token and connection URL."
  value       = aws_secretsmanager_secret.valkey.name
}

output "parameter_group_name" {
  description = "Name of the ElastiCache parameter group (valkey8)."
  value       = aws_elasticache_parameter_group.this.name
}

output "replication_group_id" {
  description = "ElastiCache replication group ID. Used as the CacheClusterId/ReplicationGroupId dimension on CloudWatch alarms."
  value       = aws_elasticache_replication_group.this.id
}
