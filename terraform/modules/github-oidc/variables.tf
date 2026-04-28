variable "github_repo" {
  description = "GitHub repository in org/repo format (e.g. lucidlink/lucidlink-connect-web-app)"
  type        = string

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.github_repo))
    error_message = "github_repo must be in org/repo format."
  }
}

variable "eks_cluster_arn" {
  description = "ARN of the EKS cluster the deploy role is permitted to describe"
  type        = string
}

variable "tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}
