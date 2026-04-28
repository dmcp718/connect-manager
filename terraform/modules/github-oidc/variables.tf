variable "github_repo" {
  description = "GitHub repository in org/repo format (e.g. dmcp718/connect-manager)."
  type        = string

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.github_repo))
    error_message = "github_repo must be in org/repo format."
  }
}

variable "branch_refs" {
  description = "List of git refs (branches and tag patterns) the deploy role's trust policy accepts. Default is the aws-fargate branch + v* release tags."
  type        = list(string)
  default     = ["refs/heads/aws-fargate", "refs/tags/v*"]
}

variable "cluster_arn" {
  description = "ARN of the ECS cluster the deploy role is scoped to (UpdateService, DescribeServices). Pass module.ecs_cluster.cluster_arn."
  type        = string
}

variable "task_role_arns" {
  description = "List of IAM role ARNs the deploy role is allowed to PassRole on (only when iam:PassedToService=ecs-tasks.amazonaws.com). Typically [execution_role_arn, web_task_role_arn, worker_task_role_arn, lucidlink_api_task_role_arn]."
  type        = list(string)
}

variable "tags" {
  description = "Additional tags to apply to all resources."
  type        = map(string)
  default     = {}
}
