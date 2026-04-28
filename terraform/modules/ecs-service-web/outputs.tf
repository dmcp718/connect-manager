output "service_name" {
  description = "ECS service name. Pass to `aws ecs update-service --service <name>` for CD deploys."
  value       = aws_ecs_service.web.name
}

output "service_arn" {
  description = "ECS service ARN."
  value       = aws_ecs_service.web.id
}

output "task_definition_arn" {
  description = "Task definition ARN at apply time. CI deploys typically register a new revision and update the service rather than re-running terraform."
  value       = aws_ecs_task_definition.web.arn
}

output "task_definition_family" {
  description = "Task definition family name."
  value       = aws_ecs_task_definition.web.family
}
