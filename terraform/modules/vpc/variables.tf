variable "cidr" {
  description = "CIDR block for the VPC. Default is distinct from aws-deploy (10.0.0.0/16) to allow eventual peering."
  type        = string
  default     = "10.20.0.0/16"
}

variable "name" {
  description = "Environment name used in resource names and tags (e.g. 'prod', 'staging', 'local')."
  type        = string
}

variable "tags" {
  description = "Additional tags merged into every taggable resource."
  type        = map(string)
  default     = {}
}
