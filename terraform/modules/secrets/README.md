# terraform/modules/secrets

Self-contained Terraform module that provisions:

- A customer-managed **KMS key** (`alias/connect-secrets`) with rotation
  enabled and a key policy granting (a) the AWS account root full admin and
  (b) the connect-eso IAM role `kms:Decrypt` + `kms:DescribeKey`.
- One **empty Secrets Manager secret** per module-owned purpose
  (`jwt`, `admin`) at `${var.name_prefix}/<purpose>`, encrypted with the KMS
  key above.

Module-owned secrets are deliberately created **without a value**. The
operator seeds plaintext via the bootstrap TUI (Epic 6) or, as a fallback,
the documented manual runbook. Terraform must not own the plaintext.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `name_prefix` | `string` | `/connect/prod` | Secrets Manager path prefix. Each secret is created at `${name_prefix}/<purpose>` (e.g. `/connect/prod/jwt`). Follows the project naming convention `/connect/<env>/<purpose>`. |
| `eso_role_arn` | `string` | _(required)_ | ARN of the connect-eso IAM role from the iam module's `role_arns.eso` output. The KMS key policy grants this principal `kms:Decrypt` and `kms:DescribeKey`. |
| `tags` | `map(string)` | `{}` | Additional tags merged onto every taggable resource. |

## Outputs

| Name | Description |
|---|---|
| `secret_arns` | Map of purpose → secret ARN (`jwt`, `admin`). |
| `secret_names` | Map of purpose → secret name (e.g. `/connect/prod/jwt`). |
| `kms_key_arn` | ARN of the `alias/connect-secrets` KMS key. Pass this into the rds and elasticache modules' `kms_key_id` so every connect secret shares one key. |
| `kms_key_id` | UUID of the KMS key (some downstream resources prefer the ID). |
| `kms_key_alias` | `alias/connect-secrets` — stable identifier for logs and dashboards. |

## Naming convention

Secret names follow `/connect/<env>/<purpose>` per CLAUDE.md "Naming". With
the default `name_prefix = "/connect/prod"` this produces:

| Purpose | Secret name | Owner |
|---|---|---|
| `jwt` | `/connect/prod/jwt` | this module |
| `admin` | `/connect/prod/admin` | this module |
| `db` | `connect/<rds-name>/db` | `terraform/modules/rds` |
| `valkey-auth` | `connect/<elc-name>/valkey` | `terraform/modules/elasticache` |

Note the slash inconsistency on the rds/elasticache side (`connect/...`,
no leading slash, includes the resource short-name segment instead of
`<env>`). That is a pre-existing artifact of those modules and is **not**
modified here — those secret names are already wired into ESO
ExternalSecret references downstream and renaming would be a separate,
coordinated change.

## Operator workflow — seeding values

1. `terraform apply` creates the empty secret containers (this module).
2. The operator runs `connect-bootstrap` (TUI, Epic 6) which:
   - Generates a strong random `JWT_SECRET_KEY` and writes it to
     `/connect/prod/jwt` as `{"value": "<64-char hex>"}`.
   - Prompts for an admin email + password and writes a bcrypt hash to
     `/connect/prod/admin` as `{"email": "...", "password_hash": "..."}`.
3. Helm install proceeds. ESO syncs the now-populated secrets into K8s
   `Secret` resources mounted by the web and worker pods.

For the manual fallback see the project README's "8-step manual runbook".

## Epic 2.4 / 2.5 cross-reference — secret deduplication

The bead spec (awsk-ux9.8) listed four purposes: `jwt`, `admin`, `db`,
`valkey-auth`. Inspection of the existing modules revealed:

- **`terraform/modules/rds/main.tf`** already creates
  `aws_secretsmanager_secret.master` with a generated password **and**
  populates `aws_secretsmanager_secret_version.master` with the live
  endpoint URL (`postgresql+asyncpg://...`). It also accepts a
  `kms_key_id` variable. Output: `master_secret_arn`.
- **`terraform/modules/elasticache/main.tf`** likewise creates
  `aws_secretsmanager_secret.valkey` plus a populated version with a
  `rediss://` URL, also KMS-encryptable. Output: `auth_secret_arn`.

Recreating those two purposes in this module would either:
1. Collide on secret name, or
2. Produce empty duplicates that ESO would have to disambiguate.

**Decision:** this module creates only `jwt` and `admin`. The root
composition (`terraform/main.tf`, Lead-owned) wires
`module.secrets.kms_key_arn` into the rds and elasticache modules'
`kms_key_id` input so all four secrets share a single customer-managed
key, even though only two are physically defined here.

The Epic 3.7 ESO `ClusterSecretStore` / `ExternalSecret` resources read
secret ARNs from the appropriate module outputs:

- `module.secrets.secret_arns["jwt"]`
- `module.secrets.secret_arns["admin"]`
- `module.rds.master_secret_arn`
- `module.elasticache.auth_secret_arn`

## KMS key policy

```hcl
# Statement 1 — account root admin (required to manage the key)
{
  "Sid": "EnableRootAccountAdmin",
  "Effect": "Allow",
  "Principal": { "AWS": "arn:aws:iam::<account-id>:root" },
  "Action": "kms:*",
  "Resource": "*"
}

# Statement 2 — ESO read-only crypto access
{
  "Sid": "AllowESORoleDecrypt",
  "Effect": "Allow",
  "Principal": { "AWS": "<var.eso_role_arn>" },
  "Action": ["kms:Decrypt", "kms:DescribeKey"],
  "Resource": "*"
}
```

ESO does **not** need `kms:Encrypt` — the bootstrap TUI uses
`PutSecretValue` which encrypts via the secret's attached KMS key under
the caller's own creds, not ESO's.

## Usage from `terraform/main.tf`

```hcl
module "iam" {
  source = "./modules/iam"
  # ...
}

module "secrets" {
  source = "./modules/secrets"

  name_prefix  = "/connect/prod"
  eso_role_arn = module.iam.role_arns.eso
  tags         = local.tags
}

module "rds" {
  source = "./modules/rds"

  # Share the same KMS key so /connect/prod/jwt, /connect/prod/admin,
  # connect/<name>/db, and connect/<name>/valkey are all encrypted with
  # alias/connect-secrets.
  kms_key_id = module.secrets.kms_key_arn
  # ...
}

module "elasticache" {
  source = "./modules/elasticache"

  kms_key_id = module.secrets.kms_key_arn
  # ...
}
```
