output "alb_arn" {
  description = "ALB ARN."
  value       = aws_lb.this.arn
}

output "alb_dns_name" {
  description = "Public DNS name for the ALB. Set a Route53 ALIAS record at var.domain pointing to this."
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "ALB hosted zone ID. Used by the Route53 ALIAS record."
  value       = aws_lb.this.zone_id
}

output "alb_arn_suffix" {
  description = "ALB ARN suffix in the form 'app/<lb-name>/<lb-id>'. Consumed by the alarms module's alb_arn_suffix input for the AWS/ApplicationELB 5xx alarm."
  value       = aws_lb.this.arn_suffix
}

output "web_target_group_arn" {
  description = "ARN of the web target group. Set as the load_balancer.target_group_arn on the web ECS Service."
  value       = aws_lb_target_group.web.arn
}

output "alb_security_group_id" {
  description = "ALB security group ID. Reference from the web-tasks SG ingress rule so only the ALB can reach web on var.web_target_port."
  value       = aws_security_group.alb.id
}
