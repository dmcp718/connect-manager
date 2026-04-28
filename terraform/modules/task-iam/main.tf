data "aws_caller_identity" "current" {}

locals {
  tags = merge({ project = "connect", env = var.env }, var.tags)

  account_id = data.aws_caller_identity.current.account_id

  # Log-group ARN pattern matches the awslogs driver convention used by the
  # ecs-service-* modules: /aws/ecs/connect-<env>/<service>.
  log_group_arn_pattern = "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/ecs/connect-${var.env}/*:*"

  # Confused-deputy mitigation for ECS Tasks: source ARN scoped to this
  # account's ECS in this region. https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html
  source_arn_pattern = "arn:aws:ecs:${var.aws_region}:${local.account_id}:*"
}

# ── Trust policy shared by Task Execution Role + per-service Task Roles ──────

data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [local.source_arn_pattern]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# ── Task Execution Role (one shared, attached to every Task Definition) ──────

resource "aws_iam_role" "execution" {
  name               = "connect-${var.env}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json

  tags = merge(local.tags, { Name = "connect-${var.env}-task-execution" })
}

# AWS-managed baseline: ECR pull + log-group create.
resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Inline addition: read connect-* secrets + decrypt with the connect KMS key.
data "aws_iam_policy_document" "execution_inline" {
  statement {
    sid       = "ECRPullScopedToConnect"
    effect    = "Allow"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    resources = var.ecr_repository_arns
  }

  statement {
    sid       = "SecretsRead"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.secret_arns
  }

  statement {
    sid       = "KMSDecrypt"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "LogsAppend"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.log_group_arn_pattern]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "connect-${var.env}-task-execution"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_inline.json
}

# ── Per-service Task Roles ───────────────────────────────────────────────────

resource "aws_iam_role" "web" {
  name               = "connect-${var.env}-task-web"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json

  tags = merge(local.tags, { Name = "connect-${var.env}-task-web" })
}

resource "aws_iam_role" "worker" {
  name               = "connect-${var.env}-task-worker"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json

  tags = merge(local.tags, { Name = "connect-${var.env}-task-worker" })
}

resource "aws_iam_role" "lucidlink_api" {
  name               = "connect-${var.env}-task-lucidlink-api"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json

  tags = merge(local.tags, { Name = "connect-${var.env}-task-lucidlink-api" })
}

# Web Task Role: write own metrics. Customer-credential S3/SQS access is NOT
# granted here — those use the customer-credential code path with STS creds
# decrypted from Postgres.
data "aws_iam_policy_document" "web_inline" {
  statement {
    sid       = "CloudWatchMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Connect/Web", "Connect/ARQ"]
    }
  }
}

resource "aws_iam_role_policy" "web" {
  name   = "connect-${var.env}-task-web"
  role   = aws_iam_role.web.id
  policy = data.aws_iam_policy_document.web_inline.json
}

# Worker Task Role: same metrics scope as web.
data "aws_iam_policy_document" "worker_inline" {
  statement {
    sid       = "CloudWatchMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Connect/Worker", "Connect/ARQ"]
    }
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "connect-${var.env}-task-worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker_inline.json
}

# lucidlink_api Task Role: no AWS-side permissions. The role exists so the
# sidecar can be tagged/audited separately and so future grants don't need a
# task-definition rewrite.
