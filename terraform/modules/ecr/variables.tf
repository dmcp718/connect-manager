variable "repo_names" {
  description = "List of ECR repository names to create. Each is a fully qualified repo name (no registry prefix)."
  type        = list(string)
  default     = ["connect-web", "connect-worker"]
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
