variable "name_prefix" {
  description = <<-EOT
    Secrets Manager path prefix. Each secret in this module is created at
    `$${var.name_prefix}/<purpose>` (e.g. `/connect/prod/jwt`). Follows the
    project naming convention `/connect/<env>/<purpose>` documented in
    CLAUDE.md.
  EOT
  type        = string
  default     = "/connect/prod"
}

variable "eso_role_arn" {
  description = <<-EOT
    ARN of the connect-eso IAM role (output from the iam module's
    `role_arns.eso`). The KMS key policy grants this principal `kms:Decrypt`
    and `kms:DescribeKey` so External Secrets Operator can decrypt secret
    values fetched from Secrets Manager.
  EOT
  type        = string
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
