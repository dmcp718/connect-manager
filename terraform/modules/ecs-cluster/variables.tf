variable "cluster_name" {
  description = "ECS cluster name (used as-is, not prefixed). Convention: connect-<env>."
  type        = string
}

variable "container_insights" {
  description = "Whether to enable Container Insights on the cluster. Required for the alarms module's pod-restart and worker queue-depth metrics."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
