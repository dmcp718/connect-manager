variable "zone_id" {
  description = "Route53 hosted zone ID that owns var.domain. The operator must create the zone and delegate it before applying this module — the module does NOT create the zone."
  type        = string

  validation {
    condition     = length(var.zone_id) > 0
    error_message = "zone_id must be a non-empty Route53 hosted zone ID (e.g. Z000000000000000000000)."
  }
}

variable "domain" {
  description = "Fully qualified domain name the certificate is issued for (e.g. connect.example.com). The certificate will also include *.<domain> as a SAN."
  type        = string

  validation {
    condition     = length(var.domain) > 0
    error_message = "domain must be a non-empty fully qualified domain name."
  }
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
