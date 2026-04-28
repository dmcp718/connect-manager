# terraform/modules/alb

Internet-facing ALB with HTTPS listener (TLS 1.3 policy), HTTP→HTTPS redirect, and an `ip`-type target group sized for Fargate web tasks.

## Resources

| Resource | Purpose |
|---|---|
| `aws_security_group.alb` | Allows public 80/443 (overridable via `var.ingress_cidrs`); egress unrestricted so ALB can reach private targets. |
| `aws_lb.this` | Internet-facing application load balancer in `var.public_subnet_ids`, idle timeout 300s (long enough for SSE). |
| `aws_lb_target_group.web` | `ip` target type (Fargate awsvpc), HTTP/`/health` probes. |
| `aws_lb_listener.http` | 80 → 301 redirect to 443. |
| `aws_lb_listener.https` | 443 → `target_group.web`, ACM cert, TLS13-1-2-2021-06 policy. |

## Inputs

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | `string` | yes | — | Deployment name. |
| `vpc_id` | `string` | yes | — | Target VPC. |
| `public_subnet_ids` | `list(string)` | yes | — | Public subnet IDs (≥2 AZs). |
| `acm_cert_arn` | `string` | yes | — | ACM cert for the HTTPS listener. |
| `ingress_cidrs` | `list(string)` | no | `["0.0.0.0/0"]` | Tighten for staging. |
| `web_target_port` | `number` | no | `8000` | Container port. |
| `web_health_check_path` | `string` | no | `/health` | TG health-check path. |
| `idle_timeout` | `number` | no | `300` | ALB idle timeout. |

## Outputs

| Name | Description |
|---|---|
| `alb_arn` | ALB ARN. |
| `alb_dns_name` | Public DNS — Route53 ALIAS this. |
| `alb_zone_id` | For the ALIAS record. |
| `alb_arn_suffix` | Pass to `alarms.alb_arn_suffix`. |
| `web_target_group_arn` | Set on the web ECS Service. |
| `alb_security_group_id` | Reference in the web-tasks ingress SG rule. |
