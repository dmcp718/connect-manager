variable "cluster_name" {
  description = "EKS cluster name (used as the ClusterName dimension on Container Insights metrics)."
  type        = string
}

variable "db_instance_id" {
  description = "RDS DB instance identifier (DBInstanceIdentifier dimension)."
  type        = string
}

variable "cache_cluster_id" {
  description = "ElastiCache replication group ID (ReplicationGroupId dimension)."
  type        = string
}

variable "alb_arn_suffix" {
  description = <<-EOT
    ALB metric dimension suffix in the form `app/<lb-name>/<lb-id>`. CloudWatch
    uses this as the `LoadBalancer` dimension on AWS/ApplicationELB metrics.
    Used only when `create_alb_alarm = true`.
  EOT
  type        = string
  default     = ""
}

variable "create_alb_alarm" {
  description = <<-EOT
    Whether to create the ALB 5xx-rate CloudWatch alarm. Default true on
    aws-fargate since the ALB is always provisioned. Set false in local /
    ministack tfvars to skip — the alarm's `count` must be a plan-time
    constant, so it cannot be derived from `alb_arn_suffix` (a module
    output, only known after apply).
  EOT
  type        = bool
  default     = true
}

variable "namespace" {
  description = "Kubernetes namespace the application pods run in (Container Insights `Namespace` dimension)."
  type        = string
  default     = "connect"
}

variable "prometheus_namespace" {
  description = <<-EOT
    CloudWatch namespace where the Prometheus → CloudWatch bridge republishes
    `connect_arq_queue_depth`. The metric is shipped via ADOT/CloudWatch Agent
    with a prometheus_exporter scrape config; see README.md for the operational
    pre-requirement.
  EOT
  type        = string
  default     = "Connect/ARQ"
}

variable "sns_topic_subscription_email" {
  description = <<-EOT
    Optional email address to subscribe to the connect-alerts SNS topic. When
    non-empty, an `aws_sns_topic_subscription` of protocol `email` is created;
    AWS sends a confirmation email that the operator must accept before alerts
    flow.
  EOT
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
