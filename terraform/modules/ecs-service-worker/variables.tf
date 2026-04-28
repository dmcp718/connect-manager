variable "env" {
  description = "Environment name."
  type        = string
}

variable "aws_region" {
  description = "AWS region. Used for the awslogs driver."
  type        = string
}

variable "cluster_arn" {
  description = "ECS cluster ARN."
  type        = string
}

variable "execution_role_arn" {
  description = "ECS Task Execution Role ARN."
  type        = string
}

variable "task_role_arn" {
  description = "Worker Task Role ARN."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the worker tasks."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security group IDs to attach to the worker tasks. Egress only — workers don't accept inbound."
  type        = list(string)
}

variable "worker_image" {
  description = "Fully qualified container image for the worker container."
  type        = string
}

variable "worker_cpu" {
  description = "Worker container vCPU units (1024 = 1 vCPU)."
  type        = number
  default     = 256
}

variable "worker_memory" {
  description = "Worker container memory MiB."
  type        = number
  default     = 512
}

variable "desired_count" {
  description = "Initial worker count. Auto Scaling owns this after creation; var.min_count is the floor (default 0 = scale-to-zero)."
  type        = number
  default     = 0
}

variable "min_count" {
  description = "Minimum worker count. Default 0 enables scale-to-zero — KEDA-equivalent for idle queues."
  type        = number
  default     = 0
}

variable "max_count" {
  description = "Maximum worker count."
  type        = number
  default     = 8
}

variable "metric_namespace" {
  description = "CloudWatch namespace where queue depth is published. Emit by module.queue_metric.metric_namespace."
  type        = string
}

variable "metric_name" {
  description = "CloudWatch metric name for queue depth. Emit by module.queue_metric.metric_name."
  type        = string
}

variable "target_queue_depth_per_worker" {
  description = "Target tracking value: average queue depth per running worker. ECS scales out when actual depth/N exceeds this; scales in when below."
  type        = number
  default     = 5
}

variable "stop_timeout" {
  description = "Seconds ECS waits between SIGTERM and SIGKILL. Set to the longest-expected-job + buffer so the ARQ graceful-drain hook can finish in-flight work. Fargate hard cap = 120s."
  type        = number
  default     = 120
}

variable "env_vars" {
  description = "Plain (non-secret) environment variables. Map of env-var name → value."
  type        = map(string)
  default     = {}
}

variable "secret_env_vars" {
  description = "Secret environment variables. Map of env-var name → Secrets Manager valueFrom."
  type        = map(string)
  default     = {}
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the worker log group."
  type        = number
  default     = 14
}

variable "use_fargate_spot" {
  description = "If true, place worker tasks on FARGATE_SPOT. Default true — workers tolerate interruption due to ARQ + processed_jobs idempotency."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
