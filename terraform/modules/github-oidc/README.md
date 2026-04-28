# terraform/modules/github-oidc

GitHub Actions → AWS IAM federation. Creates the OIDC provider for `token.actions.githubusercontent.com` and a least-privilege deploy role scoped to the `aws-fargate` branch + `v*` release tags.

## What the role can do

| Sid | Actions | Resource |
|---|---|---|
| `ECRAuthToken` | `ecr:GetAuthorizationToken` | `*` (token has no resource scope in the API) |
| `ECRRepositoryAccess` | push/pull on `connect-*` repos | `arn:aws:ecr:*:<account>:repository/connect-*` |
| `ECSReadOnly` | `ecs:Describe*`, `ecs:List*`, `sts:GetCallerIdentity` | `*` (most ECS reads don't support resource-level constraints) |
| `ECSRegisterTaskDefinition` | `ecs:RegisterTaskDefinition` | `*` (action doesn't support resource constraints — `iam:PassRole` is the real gate) |
| `ECSDeployToCluster` | `ecs:UpdateService`, `ecs:RunTask`, `ecs:StopTask` | `var.cluster_arn` + service/task/task-definition siblings under it |
| `PassECSTaskRoles` | `iam:PassRole` | `var.task_role_arns` (only when `iam:PassedToService=ecs-tasks.amazonaws.com`) |

## Usage

```hcl
module "github_oidc" {
  source = "./modules/github-oidc"

  github_repo    = "dmcp718/connect-manager"
  cluster_arn    = module.ecs_cluster.cluster_arn
  task_role_arns = concat(
    [module.task_iam.execution_role_arn],
    values(module.task_iam.role_arns),
  )

  tags = local.tags
}
```

## Inputs

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `github_repo` | `string` | yes | — | `org/repo`. |
| `branch_refs` | `list(string)` | no | `["refs/heads/aws-fargate", "refs/tags/v*"]` | Trust-policy refs. |
| `cluster_arn` | `string` | yes | — | Scopes mutating ECS actions. |
| `task_role_arns` | `list(string)` | yes | — | Scopes `iam:PassRole`. |
| `tags` | `map(string)` | no | `{}` | |

## Outputs

| Name | Description |
|---|---|
| `role_arn` | The role ARN — add to GitHub repo secrets as `AWS_ROLE_ARN`. |
| `role_name` | Role name — for attaching extra policies at root. |
| `oidc_provider_arn` | OIDC provider ARN. |

## Operator follow-up

After `terraform apply`, push the role ARN to the GitHub repo:

```bash
terraform output -raw github_actions_role_arn | gh secret set AWS_ROLE_ARN -R dmcp718/connect-manager
```

The CI workflow at `.github/workflows/aws-fargate.yml` references `${{ secrets.AWS_ROLE_ARN }}` for `aws-actions/configure-aws-credentials`.
