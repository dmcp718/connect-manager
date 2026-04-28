output "cluster_id" {
  description = "ECS cluster ID (same as the cluster ARN for this resource)."
  value       = aws_ecs_cluster.this.id
}

output "cluster_arn" {
  description = "ECS cluster ARN. Used by the github-oidc deploy role for ecs:UpdateService scoping."
  value       = aws_ecs_cluster.this.arn
}

output "cluster_name" {
  description = "ECS cluster name. Used as the ClusterName dimension on Container Insights alarms."
  value       = aws_ecs_cluster.this.name
}
