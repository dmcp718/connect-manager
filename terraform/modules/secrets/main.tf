locals {
  tags = merge({ project = "connect" }, var.tags)

  # Purposes this module owns. The RDS module (Epic 2.4) owns `db` and the
  # ElastiCache module (Epic 2.5) owns `valkey-auth`; both create their own
  # `aws_secretsmanager_secret` populated with a generated password and the
  # live endpoint URL. This module deliberately does NOT recreate those —
  # see README.md "Epic 2.4 / 2.5 cross-reference" for the rationale.
  purposes = toset(["jwt", "admin"])
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# KMS — customer-managed key for envelope encryption of all connect secrets
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "kms" {
  # (a) Account root has full administrative control. Without this statement
  # the key becomes unmanageable.
  statement {
    sid    = "EnableRootAccountAdmin"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    actions   = ["kms:*"]
    resources = ["*"]
  }

  # (b) ESO role can decrypt and inspect secrets, nothing more.
  statement {
    sid    = "AllowESORoleDecrypt"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = [var.eso_role_arn]
    }

    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
    ]
    resources = ["*"]
  }
}

resource "aws_kms_key" "this" {
  description             = "connect: envelope encryption key for Secrets Manager secrets"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.kms.json

  tags = merge(local.tags, { Name = "connect-secrets" })
}

resource "aws_kms_alias" "this" {
  name          = "alias/connect-secrets"
  target_key_id = aws_kms_key.this.key_id
}

# ---------------------------------------------------------------------------
# Secrets Manager — empty secret containers; values are seeded out-of-band
# by the bootstrap TUI (Epic 6) or, as a fallback, the operator runbook.
# We intentionally do NOT create aws_secretsmanager_secret_version resources:
# Terraform must not own the plaintext.
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "this" {
  for_each = local.purposes

  name        = "${var.name_prefix}/${each.value}"
  description = "connect ${each.value} secret (seeded by bootstrap TUI)"
  kms_key_id  = aws_kms_key.this.arn

  tags = merge(local.tags, { Name = "${var.name_prefix}/${each.value}" })
}
