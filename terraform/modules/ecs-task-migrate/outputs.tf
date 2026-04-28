output "task_definition_arn" {
  description = "Task definition ARN. CI runs this via `aws ecs run-task --task-definition <arn>`."
  value       = aws_ecs_task_definition.migrate.arn
}

output "task_definition_family" {
  description = "Task definition family name."
  value       = aws_ecs_task_definition.migrate.family
}
