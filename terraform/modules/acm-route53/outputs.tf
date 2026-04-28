output "acm_cert_arn" {
  description = "ARN of the validated ACM certificate. Resolves only after DNS validation has completed, so downstream resources (e.g. ALB HTTPS listener) that depend on this output will block until the certificate is ISSUED."
  value       = aws_acm_certificate_validation.this.certificate_arn
}

output "validated" {
  description = "Marker output for downstream module ordering. Always true once validation completes — depend on this output to gate resources that must wait for the certificate."
  value       = true
  depends_on  = [aws_acm_certificate_validation.this]
}
