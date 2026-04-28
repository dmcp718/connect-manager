# terraform/modules/acm-route53

Self-contained Terraform module that issues a DNS-validated ACM certificate
covering both `var.domain` and the wildcard `*.<domain>`, then writes the
validation CNAME records into an **operator-supplied** Route53 hosted zone
and waits for the certificate to reach `ISSUED`.

## Operator pre-requisite

The Route53 **public hosted zone for the apex of `var.domain` must already
exist** in the same AWS account this module runs in, and the operator must
have delegated that zone at the registrar (NS records pointing at the four
Route53 nameservers Amazon assigned).

This module deliberately does **not** create the zone:

- Hosted zones are billable per-month and frequently shared across
  environments — destroying this module should not destroy the zone.
- Apex zones almost always require manual NS delegation at the registrar,
  which Terraform cannot automate.
- Route53 hosted zone IDs are stable; treating the zone as an external
  dependency lets multiple environments (`prod`, `staging`) share a parent
  zone without coordinating Terraform state.

The operator passes the existing zone's ID via `var.zone_id`.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `zone_id` | `string` | _(required)_ | Route53 hosted zone ID that owns `var.domain` (e.g. `Z000000000000000000000`). |
| `domain` | `string` | _(required)_ | Fully qualified domain name the certificate is issued for (e.g. `connect.example.com`). The certificate also includes `*.<domain>` as a SAN. |
| `tags` | `map(string)` | `{}` | Additional tags merged onto every taggable resource. |

## Outputs

| Name | Description |
|---|---|
| `acm_cert_arn` | ARN of the validated ACM certificate. Resolves only after DNS validation completes, so any resource referencing this output (e.g. an ALB HTTPS listener) blocks until the certificate is `ISSUED`. |
| `validated` | Constant `true`, but carries an explicit `depends_on` against `aws_acm_certificate_validation`. Use it as a gate for downstream modules that must wait for the certificate. |

## Resources

- `aws_acm_certificate.this` — DNS-validated certificate, `domain_name=var.domain`, with `*.${var.domain}` in `subject_alternative_names`. `create_before_destroy = true` so SAN changes don't drop the in-use cert from the ALB listener mid-rotation.
- `aws_route53_record.validation` — one CNAME per entry in `domain_validation_options`, TTL 60, `allow_overwrite = true` so re-runs after partial failure don't error on existing records.
- `aws_acm_certificate_validation.this` — completes only when ACM has confirmed every validation CNAME, providing the "ready" signal exposed via `acm_cert_arn` and `validated`.

## Usage from `terraform/main.tf`

```hcl
module "acm_route53" {
  source = "./modules/acm-route53"

  zone_id = var.route53_zone_id  # operator-supplied via terraform.tfvars
  domain  = var.connect_domain   # e.g. "connect.example.com"
  tags    = local.tags
}

# Downstream: ALB HTTPS listener consumes the validated cert ARN.
resource "aws_lb_listener" "https" {
  # ...
  certificate_arn = module.acm_route53.acm_cert_arn
}
```

## Local / ministack note

ACM and Route53 against ministack are not exercised in the local-first dev
loop — the module is validated by `terraform validate` and `terraform plan`
only. End-to-end issuance + DNS-01 validation runs only in the Phase 4
`awsk-polish.realaws-smoke` task, against real AWS.
