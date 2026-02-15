variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "lucidlink-connect"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "domain" {
  description = "Domain name for the application (e.g., connect.example.com)"
  type        = string
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone ID for the domain"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.small"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "allowed_cidrs" {
  description = "CIDR blocks allowed to access the ALB (restrict for security)"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
