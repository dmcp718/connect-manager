variable "env" {
  description = "Environment name (used in resource names + CloudWatch log groups)."
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
  description = "ECS Task Execution Role ARN. Emitted by module.task_iam.execution_role_arn."
  type        = string
}

variable "task_role_arn" {
  description = "Web Task Role ARN. Emitted by module.task_iam.role_arns.web."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the web tasks (Fargate awsvpc). Should span ≥2 AZs."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security group IDs to attach to the web tasks. Typically a single SG that ingresses from the ALB SG only."
  type        = list(string)
}

variable "target_group_arn" {
  description = "ARN of the ALB target group the service registers tasks with. Emitted by module.alb.web_target_group_arn."
  type        = string
}

variable "web_image" {
  description = "Fully qualified container image for the web container (e.g. <ecr-uri>:<tag>)."
  type        = string
}

variable "lucidlink_api_image" {
  description = "Container image for the lucidlink-api sidecar."
  type        = string
  default     = "lucidlink/lucidlink-api:latest"
}

variable "web_port" {
  description = "Port the web container listens on. Must match alb.web_target_port."
  type        = number
  default     = 8000
}

variable "web_cpu" {
  description = "Web container vCPU units (1024 = 1 vCPU). Sets the task-level cpu when summed with the sidecar."
  type        = number
  default     = 512
}

variable "web_memory" {
  description = "Web container memory MiB. Sets the task-level memory when summed with the sidecar."
  type        = number
  default     = 1024
}

variable "lucidlink_api_cpu" {
  description = "lucidlink-api sidecar vCPU units."
  type        = number
  default     = 256
}

variable "lucidlink_api_memory" {
  description = "lucidlink-api sidecar memory MiB."
  type        = number
  default     = 512
}

variable "lucidlink_api_port" {
  description = "Port the lucidlink-api sidecar listens on. Web reaches it via http://localhost:<port>/api/v1."
  type        = number
  default     = 3003
}

variable "desired_count" {
  description = "Initial / steady-state number of web tasks. Auto Scaling can grow it up to var.max_count."
  type        = number
  default     = 2
}

variable "min_count" {
  description = "Minimum web task count under Auto Scaling."
  type        = number
  default     = 2
}

variable "max_count" {
  description = "Maximum web task count under Auto Scaling."
  type        = number
  default     = 6
}

variable "target_cpu_utilization" {
  description = "Target CPU % for Service Auto Scaling target tracking."
  type        = number
  default     = 60
}

variable "stop_timeout" {
  description = "Seconds ECS waits between SIGTERM and SIGKILL. Set high enough for graceful drain (FastAPI shutdown + in-flight request drain). Hard cap is 120s on Fargate."
  type        = number
  default     = 30
}

variable "env_vars" {
  description = "Plain (non-secret) environment variables for the web container. Map of env-var name → value."
  type        = map(string)
  default     = {}
}

variable "secret_env_vars" {
  description = "Secret environment variables for the web container. Map of env-var name → valueFrom string (typically '<secret-arn>:<json-key>::' to extract a single field)."
  type        = map(string)
  default     = {}
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for web + lucidlink-api log groups."
  type        = number
  default     = 14
}

variable "use_fargate_spot" {
  description = "If true, place web tasks on FARGATE_SPOT. Default false because web mid-request termination is user-visible."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
