output "execution_role_arn" {
  description = "ARN of the Task Execution Role. Set as executionRoleArn on every Task Definition."
  value       = aws_iam_role.execution.arn
}

output "role_arns" {
  description = "Map of service key → Task Role ARN. Set as taskRoleArn on the corresponding Task Definition."
  value = {
    web           = aws_iam_role.web.arn
    worker        = aws_iam_role.worker.arn
    lucidlink_api = aws_iam_role.lucidlink_api.arn
  }
}
