variable "env" {
  description = "Environment name (e.g. prod, dev). Used in role names and log-group ARN scoping."
  type        = string
}

variable "aws_region" {
  description = "AWS region. Used for region-scoped log-group ARNs and the source-arn confused-deputy condition."
  type        = string
}

variable "ecr_repository_arns" {
  description = "List of ECR repository ARNs the Task Execution Role is allowed to pull from. Typically values(module.ecr.repository_arns)."
  type        = list(string)
}

variable "secret_arns" {
  description = "List of Secrets Manager secret ARNs the Task Execution Role is allowed to read. Includes the rds master secret, the elasticache auth secret, and module.secrets-emitted secret ARNs."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "ARN of the customer-managed KMS key encrypting connect secrets (alias/connect-secrets). Granted kms:Decrypt + kms:DescribeKey on the Task Execution Role."
  type        = string
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
