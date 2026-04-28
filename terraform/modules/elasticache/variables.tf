variable "name" {
  description = "Deployment name — used in resource names, tags, and the Secrets Manager path (connect/<name>/valkey)."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the ElastiCache subnet group (should span at least 2 AZs)."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security group IDs to attach to the ElastiCache replication group."
  type        = list(string)
}

variable "node_type" {
  description = "ElastiCache node type."
  type        = string
  default     = "cache.t4g.micro"
}

variable "engine_version" {
  description = "Valkey engine version. Pinned to a minor version; review on major Valkey releases."
  type        = string
  default     = "8.0"
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
