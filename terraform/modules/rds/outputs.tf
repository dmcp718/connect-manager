output "endpoint" {
  description = "RDS instance endpoint (host:port). Use the host component for the DB URL."
  value       = aws_db_instance.this.endpoint
}

output "port" {
  description = "RDS instance port (always 5432 for Postgres)."
  value       = 5432
}

output "db_name" {
  description = "Name of the initial database on the instance."
  value       = aws_db_instance.this.db_name
}

output "master_secret_arn" {
  description = "ARN of the Secrets Manager secret holding master credentials. Consumed by ESO ExternalSecret in Epic 3.7."
  value       = aws_secretsmanager_secret.master.arn
}

output "master_secret_name" {
  description = "Name of the Secrets Manager secret holding master credentials."
  value       = aws_secretsmanager_secret.master.name
}

output "parameter_group_name" {
  description = "Name of the DB parameter group (postgres16, rds.force_ssl=1)."
  value       = aws_db_parameter_group.this.name
}

output "db_instance_id" {
  description = "RDS DB instance identifier. Used as the DBInstanceIdentifier dimension on CloudWatch alarms."
  value       = aws_db_instance.this.identifier
}
