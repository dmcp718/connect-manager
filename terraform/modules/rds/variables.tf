variable "name" {
  description = "Deployment name — used in resource names, tags, and the Secrets Manager path (connect/<name>/db)."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the DB subnet group (should span at least 2 AZs)."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security group IDs to attach to the RDS instance."
  type        = list(string)
}

variable "db_name" {
  description = "Name of the initial database created on the instance."
  type        = string
  default     = "connect"
}

variable "username" {
  description = "Master username for the RDS instance."
  type        = string
  default     = "connect"
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage" {
  description = "Allocated storage in GiB."
  type        = number
  default     = 20
}

variable "multi_az" {
  description = "Whether to enable Multi-AZ for the RDS instance. Flip to true for production tiers."
  type        = bool
  default     = false
}

variable "engine_version" {
  description = "Postgres engine version. Pinned to a minor version; review on major Postgres releases."
  type        = string
  default     = "16.4"
}

variable "kms_key_id" {
  description = "KMS key ARN or ID for Secrets Manager encryption. Empty string uses the AWS-managed key (alias/aws/secretsmanager)."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
