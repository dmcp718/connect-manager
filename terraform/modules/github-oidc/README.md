# terraform/modules/github-oidc

Creates the AWS IAM OIDC provider for GitHub Actions and a least-privilege deploy role scoped to the `aws-kubernetes` branch and `v*` release tags.

## Usage

```hcl
module "github_oidc" {
  source = "./modules/github-oidc"

  github_repo     = "lucidlink/lucidlink-connect-web-app"
  eks_cluster_arn = module.eks.cluster_arn
  tags            = local.common_tags
}
```

## Inputs

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `github_repo` | `string` | yes | GitHub repository in `org/repo` format (e.g. `lucidlink/lucidlink-connect-web-app`) |
| `eks_cluster_arn` | `string` | yes | ARN of the EKS cluster the deploy role is permitted to describe |
| `tags` | `map(string)` | no | Additional tags applied to all resources (default: `{}`) |

## Outputs

| Name | Description |
|------|-------------|
| `role_arn` | ARN of the `connect-github-actions` IAM role — store in GitHub repo secrets |
| `oidc_provider_arn` | ARN of the GitHub Actions OIDC provider |

## Operator follow-up

After `terraform apply`, add the role ARN to the GitHub repository secrets:

1. Get the ARN:
   ```bash
   terraform output -raw github_oidc_role_arn
   ```
2. Add it to the repository at **Settings → Secrets and variables → Actions**:
   - **Name:** `AWS_ROLE_ARN`
   - **Value:** the ARN from step 1

The GitHub Actions workflow (`ci.yml`) references `${{ secrets.AWS_ROLE_ARN }}` for the `aws-actions/configure-aws-credentials` step.

> **Lead bead required:** File a bead to track adding `AWS_ROLE_ARN` to GitHub repo secrets once `terraform apply` completes in the Phase 4 real-AWS smoke run. The bead should block any CD smoke test that pushes an image to ECR.
