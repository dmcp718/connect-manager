output "lambda_function_arn" {
  description = "Lambda function ARN."
  value       = aws_lambda_function.this.arn
}

output "lambda_function_name" {
  description = "Lambda function name."
  value       = aws_lambda_function.this.function_name
}

output "lambda_security_group_id" {
  description = "Security group ID of the Lambda. Reference from the Valkey SG ingress rule so the Lambda can reach the replication group on 6379."
  value       = aws_security_group.lambda.id
}

output "metric_namespace" {
  description = "CloudWatch namespace for the published metric. Pass to alarms.prometheus_namespace and to the worker ECS Service's TargetTrackingScalingPolicy."
  value       = var.metric_namespace
}

output "metric_name" {
  description = "CloudWatch metric name."
  value       = var.metric_name
}
