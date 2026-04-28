# terraform/modules/vpc

3-AZ VPC with public and private subnets, a single NAT Gateway (cost-optimised), and an Internet Gateway. Intended for use by the `aws-kubernetes` EKS deployment.

## Usage

```hcl
module "vpc" {
  source = "./modules/vpc"

  name = "prod"
  cidr = "10.20.0.0/16"
  tags = { owner = "platform" }
}
```

## Subnet layout (default CIDR `10.20.0.0/16`)

| Subnet | CIDR | AZ |
|--------|------|----|
| public-a | 10.20.0.0/20 | AZ 0 |
| public-b | 10.20.16.0/20 | AZ 1 |
| public-c | 10.20.32.0/20 | AZ 2 |
| private-a | 10.20.48.0/20 | AZ 3 offset 0 |
| private-b | 10.20.64.0/20 | AZ 3 offset 1 |
| private-c | 10.20.80.0/20 | AZ 3 offset 2 |

All private subnets route through the single NAT Gateway in AZ-a.

## Inputs

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `name` | `string` | (required) | Environment name used in resource names/tags. |
| `cidr` | `string` | `10.20.0.0/16` | VPC CIDR block. Distinct from aws-deploy (`10.0.0.0/16`) for safe peering. |
| `tags` | `map(string)` | `{}` | Additional tags merged into every resource. |

## Outputs

| Name | Description |
|------|-------------|
| `vpc_id` | VPC ID. |
| `public_subnet_ids` | List of three public subnet IDs. |
| `private_subnet_ids` | List of three private subnet IDs. |
| `nat_eip` | Public IP of the NAT Gateway EIP (AZ-a). |

## Notes

- Every resource receives `project = "connect"` and `env = var.name` tags, plus any extras from `var.tags`.
- Public subnets carry `kubernetes.io/role/elb = 1` and private subnets carry `kubernetes.io/role/internal-elb = 1` for ALB controller subnet auto-discovery.
- No security groups — those live in their own modules.
