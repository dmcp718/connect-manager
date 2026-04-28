data "aws_caller_identity" "current" {}

locals {
  tags          = merge({ project = "connect", env = var.env }, var.tags)
  function_name = "connect-${var.env}-queue-metric"
  log_group     = "/aws/lambda/${local.function_name}"
}

# ── Lambda code zip ──────────────────────────────────────────────────────────

data "archive_file" "lambda" {
  type        = "zip"
  source_file = "${path.module}/lambda/index.py"
  output_path = "${path.module}/lambda/index.zip"
}

# ── Security group: egress only (the data-plane SG ingress is wired at root) ─

resource "aws_security_group" "lambda" {
  name        = "connect-${var.env}-queue-metric"
  description = "queue-metric Lambda. Egress to Valkey 6379 only. Ingress from this SG is added at root on the Valkey SG."
  vpc_id      = var.vpc_id

  egress {
    description = "Valkey TLS"
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # Pinned to Valkey via the Valkey SG ingress, not here.
  }

  egress {
    description = "AWS APIs over HTTPS (Secrets Manager, CloudWatch via VPC endpoints or NAT)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "connect-${var.env}-queue-metric" })
}

# ── IAM role ─────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = local.function_name
  assume_role_policy = data.aws_iam_policy_document.assume.json

  tags = merge(local.tags, { Name = local.function_name })
}

# VPC ENI permissions for in-VPC Lambda.
resource "aws_iam_role_policy_attachment" "vpc" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "lambda_inline" {
  statement {
    sid       = "ReadValkeySecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.valkey_secret_arn]
  }

  statement {
    sid       = "DecryptSecretWithCMK"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "PublishMetric"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [var.metric_namespace]
    }
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = local.function_name
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_inline.json
}

# ── Log group (created up front so the Lambda doesn't auto-create with infinite retention) ──

resource "aws_cloudwatch_log_group" "lambda" {
  name              = local.log_group
  retention_in_days = var.log_retention_days

  tags = merge(local.tags, { Name = local.log_group })
}

# ── Lambda function ──────────────────────────────────────────────────────────

resource "aws_lambda_function" "this" {
  function_name    = local.function_name
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "index.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 10
  memory_size      = 128

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      VALKEY_SECRET_ARN = var.valkey_secret_arn
      METRIC_NAMESPACE  = var.metric_namespace
      METRIC_NAME       = var.metric_name
      QUEUE_KEY         = var.queue_key
      ENV               = var.env
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.vpc,
    aws_cloudwatch_log_group.lambda,
  ]

  tags = merge(local.tags, { Name = local.function_name })
}

# ── EventBridge schedule (1/min) ─────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${local.function_name}-schedule"
  description         = "Trigger queue-metric Lambda on a fixed cadence so worker autoscaling has a metric to react to even when scaled to zero."
  schedule_expression = var.schedule_expression

  tags = merge(local.tags, { Name = "${local.function_name}-schedule" })
}

resource "aws_cloudwatch_event_target" "schedule" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "lambda"
  arn       = aws_lambda_function.this.arn
}

resource "aws_lambda_permission" "schedule" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
