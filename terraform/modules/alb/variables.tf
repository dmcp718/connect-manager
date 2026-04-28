variable "name" {
  description = "Deployment name. Used in resource names + tags."
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC the ALB lives in."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs the internet-facing ALB attaches to (one per AZ)."
  type        = list(string)
}

variable "acm_cert_arn" {
  description = "ARN of the ACM certificate for the HTTPS listener. Issued + validated by the acm-route53 module."
  type        = string
}

variable "ingress_cidrs" {
  description = "CIDR blocks allowed to reach 80/443 on the ALB. Default 0.0.0.0/0 = public; tighten for staging/internal-only."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "web_target_port" {
  description = "Port the web container listens on. Set as the target group port."
  type        = number
  default     = 8000
}

variable "web_health_check_path" {
  description = "HTTP path for the target group's health check. Must return 2xx for the target to be marked healthy."
  type        = string
  default     = "/health"
}

variable "idle_timeout" {
  description = "ALB idle timeout in seconds. Bumped from 60 to support SSE/long-poll endpoints."
  type        = number
  default     = 300
}

variable "tags" {
  description = "Additional tags merged onto every taggable resource."
  type        = map(string)
  default     = {}
}
