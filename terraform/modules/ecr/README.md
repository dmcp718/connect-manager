# terraform/modules/ecr

Self-contained Terraform module that provisions one ECR repository per name in
`var.repo_names`, each configured with:

- `image_tag_mutability = MUTABLE` — required so floating tags like
  `:latest-aws-kubernetes` and `:main` can be re-pushed by CI.
- `scan_on_push = true` — Amazon ECR basic scanning runs on every push.
- A lifecycle policy that keeps the last **50 tagged** images (matching tag
  prefixes `v`, `latest`, `main`, `aws-kubernetes`) and expires **untagged**
  images beyond the most recent 30.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `repo_names` | `list(string)` | `["connect-web", "connect-worker"]` | ECR repository names to create. |
| `tags` | `map(string)` | `{}` | Additional tags merged onto every taggable resource. |

## Outputs

| Name | Description |
|---|---|
| `repository_uris` | Map of repository name → repository URI (push target for `docker push`). |
| `repository_arns` | Map of repository name → repository ARN. |
| `repository_names` | Sorted list of created repository names. |

## Lifecycle policy

The policy is rendered as a single JSON document containing two rules:

```json
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Keep last 50 tagged images",
      "selection": {
        "tagStatus": "tagged",
        "tagPrefixList": ["v", "latest", "main", "aws-kubernetes"],
        "countType": "imageCountMoreThan",
        "countNumber": 50
      },
      "action": { "type": "expire" }
    },
    {
      "rulePriority": 2,
      "description": "Expire untagged images beyond the last 30",
      "selection": {
        "tagStatus": "untagged",
        "countType": "imageCountMoreThan",
        "countNumber": 30
      },
      "action": { "type": "expire" }
    }
  ]
}
```

`rulePriority = 1` runs first so tagged images are preserved before the
untagged-sweep rule has a chance to reap them.

## Usage from `terraform/main.tf`

```hcl
module "ecr" {
  source = "./modules/ecr"

  repo_names = ["connect-web", "connect-worker"]
  tags       = local.tags
}
```

Downstream consumers (CI publish workflow, Helm `image.repository` values) read
`module.ecr.repository_uris["connect-web"]`.
