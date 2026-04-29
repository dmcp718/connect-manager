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
  description = "Name of the initial database created on the instance. 'connect' (the obvious default) is a Postgres reserved word and rejected by RDS, so we use 'connectdb'."
  type        = string
  default     = "connectdb"
}

variable "username" {
  description = "Master username for the RDS instance."
  type        = string
  default     = "connectadmin"
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
  description = "Postgres engine version. Pinned to a minor version; review when AWS retires the version."
  type        = string
  default     = "16.13"
}

variable "deletion_protection" {
  description = "Whether the RDS instance has deletion protection enabled. Default true for prod; smokes (awsk-3r4.5-style one-shot deploys) MUST set false in tfvars or terraform destroy will fail at the delete step. Operator can also disable on the live instance via `aws rds modify-db-instance --no-deletion-protection` and re-run destroy."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "Skip the final RDS snapshot on delete. Default false (production-style — keep the snapshot). Smokes set true to avoid the snapshot artifact + the few minutes it takes to create."
  type        = bool
  default     = false
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
