variable "env" {
  description = "Environment name. Set as the Env dimension on emitted metrics."
  type        = string
}

variable "aws_region" {
  description = "AWS region. Used for log-group ARN scoping."
  type        = string
}

variable "valkey_secret_arn" {
  description = "ARN of the Secrets Manager secret holding {host, port, auth_token, url} for the Valkey replication group. Emitted by module.elasticache.auth_secret_arn."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the connect-secrets CMK. Required for kms:Decrypt when reading the Valkey secret."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID (Lambda runs in-VPC because Valkey lives in private subnets)."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs the Lambda's ENI is attached to. Should span ≥2 AZs so the Lambda follows ElastiCache failover."
  type        = list(string)
}

variable "queue_key" {
  description = "Valkey list key holding the ARQ work queue. ARQ default is 'arq:queue'."
  type        = string
  default     = "arq:queue"
}

variable "schedule_expression" {
  description = "EventBridge schedule expression. Default = once per minute."
  type        = string
  default     = "rate(1 minute)"
}

variable "metric_namespace" {
  description = "CloudWatch namespace for the published metric. The alarms module reads this in var.prometheus_namespace."
  type        = string
  default     = "Connect/ARQ"
}

variable "metric_name" {
  description = "CloudWatch metric name. Used in the worker ECS Service's TargetTrackingScalingPolicy."
  type        = string
  default     = "connect_arq_queue_depth"
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the Lambda. Default 7d (the metric is the durable artifact, not the logs)."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
