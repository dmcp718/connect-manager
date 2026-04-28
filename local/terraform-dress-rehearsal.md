# Terraform dress rehearsal against ministack

Plans the entire `aws-fargate` Terraform stack against ministack/LocalStack
without spending a cent on AWS. Catches the bugs that would otherwise hit
the operator during the real-AWS smoke (`awsk-3r4.5`).

## Prerequisites

- ministack running on `localhost:4566` (`docker compose -f local/docker-compose.local.yml up -d` or however you bring it up)
- `terraform` ≥ 1.5 on `$PATH` (install via `curl -fsSL -o /tmp/tf.zip https://releases.hashicorp.com/terraform/1.6.6/terraform_1.6.6_linux_amd64.zip && unzip -d ~/.local/bin /tmp/tf.zip`)

## Recipe

Drop a throwaway `local.tfvars` (don't commit) and a `local-ministack-override.tf`
(also don't commit — they conflict with the real-AWS provider config):

```bash
cat > terraform/local.tfvars <<'EOF'
domain           = "connect.local"
route53_zone_id  = "Z000000LOCAL"
github_repo      = ""
alarms_email     = "alerts@example.com"
env              = "local"
create_alb_alarm = false   # ministack doesn't fully implement ALB metrics
EOF
```

The provider override goes in a temporary file alongside `main.tf`. Two
options — **pick one**, do not use both:

### Option A — env vars only (cleanest, no temp file)

```bash
cd terraform/
TF_DATA_DIR=/tmp/tfdata \
  AWS_ENDPOINT_URL=http://localhost:4566 \
  AWS_ACCESS_KEY_ID=test \
  AWS_SECRET_ACCESS_KEY=test \
  AWS_DEFAULT_REGION=us-east-1 \
  terraform init -input=false

TF_DATA_DIR=/tmp/tfdata \
  AWS_ENDPOINT_URL=http://localhost:4566 \
  AWS_ACCESS_KEY_ID=test \
  AWS_SECRET_ACCESS_KEY=test \
  AWS_DEFAULT_REGION=us-east-1 \
  terraform plan -var-file=local.tfvars -input=false
```

`TF_DATA_DIR=/tmp/tfdata` sidesteps a permission issue if `.terraform/` was
ever written by a containerized terraform run (root-owned).

### Option B — explicit `endpoints` block (older terraform-aws versions)

If terraform-provider-aws < 5.40 is in use, `AWS_ENDPOINT_URL` isn't honored.
Drop a `local-ministack-override.tf` next to `main.tf` and **comment out the
existing `provider "aws"` block in main.tf** (terraform forbids two
non-aliased provider configs).

```hcl
provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  s3_use_path_style           = true

  endpoints {
    acm                    = "http://localhost:4566"
    apigateway             = "http://localhost:4566"
    cloudwatch             = "http://localhost:4566"
    cloudwatchlogs         = "http://localhost:4566"
    dynamodb               = "http://localhost:4566"
    ec2                    = "http://localhost:4566"
    ecr                    = "http://localhost:4566"
    ecs                    = "http://localhost:4566"
    elasticache            = "http://localhost:4566"
    elasticloadbalancing   = "http://localhost:4566"
    elasticloadbalancingv2 = "http://localhost:4566"
    iam                    = "http://localhost:4566"
    kms                    = "http://localhost:4566"
    lambda                 = "http://localhost:4566"
    rds                    = "http://localhost:4566"
    route53                = "http://localhost:4566"
    s3                     = "http://localhost:4566"
    secretsmanager         = "http://localhost:4566"
    sns                    = "http://localhost:4566"
    sqs                    = "http://localhost:4566"
    ssm                    = "http://localhost:4566"
    sts                    = "http://localhost:4566"
  }
}
```

## What "success" looks like

`terraform plan` exits 0 with `Plan: N to add, 0 to change, 0 to destroy.`
The actual `N` will fluctuate as modules grow; on the day this doc landed it
was 94.

## What "success" does NOT prove

ministack's coverage is partial — it accepts most resource shapes but skips
some semantic validation. Things the dress rehearsal **does not** catch:

- ACM cert DNS-01 challenge round-trip
- ALB target-group health under load (no real traffic)
- RDS backup window / snapshot creation
- ECS task IAM execution permissions (ministack accepts any role ARN)
- CloudWatch metric publishing rate (no real metrics flow)

Those checks live in the real-AWS smoke (`awsk-3r4.5` AC §3) and can't
be moved earlier without spending real money.

## Cleanup

```bash
rm terraform/local.tfvars terraform/local-ministack-override.tf
rm -rf /tmp/tfdata
```

If you used Option B, restore the original `provider "aws"` block in
`main.tf`.
