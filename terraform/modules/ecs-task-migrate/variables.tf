variable "env" {
  description = "Environment name."
  type        = string
}

variable "aws_region" {
  description = "AWS region."
  type        = string
}

variable "execution_role_arn" {
  description = "ECS Task Execution Role ARN."
  type        = string
}

variable "task_role_arn" {
  description = "Task Role ARN. Reuses the web role since migrations need DATABASE_URL only."
  type        = string
}

variable "web_image" {
  description = "Container image to run the migration in. Should be the web image (Dockerfile COPYs alembic.ini + alembic/ to /migrations)."
  type        = string
}

variable "command" {
  description = "Container command override. Default runs 'alembic upgrade head' against /migrations/alembic.ini."
  type        = list(string)
  default     = ["alembic", "-c", "/migrations/alembic.ini", "upgrade", "head"]
}

variable "cpu" {
  description = "Task vCPU units."
  type        = number
  default     = 256
}

variable "memory" {
  description = "Task memory MiB."
  type        = number
  default     = 512
}

variable "env_vars" {
  description = "Plain environment variables for the migration task."
  type        = map(string)
  default     = {}
}

variable "secret_env_vars" {
  description = "Secret environment variables (Secrets Manager valueFrom). Must include DATABASE_URL."
  type        = map(string)
  default     = {}
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the migrate log group."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
