data "aws_caller_identity" "current" {}

data "tls_certificate" "github_actions" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github_actions.certificates[0].sha1_fingerprint]

  tags = merge(
    {
      Name = "connect-github-actions-oidc"
    },
    var.tags
  )
}

data "aws_iam_policy_document" "github_actions_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for ref in var.branch_refs : "repo:${var.github_repo}:ref:${ref}"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "connect-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume_role.json

  tags = merge(
    {
      Name = "connect-github-actions"
    },
    var.tags
  )
}

data "aws_iam_policy_document" "github_actions_inline" {
  statement {
    sid       = "ECRAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "ECRRepositoryAccess"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
    ]
    resources = ["arn:aws:ecr:*:${data.aws_caller_identity.current.account_id}:repository/connect-*"]
  }

  # Most ECS Describe/List actions don't support resource-level constraints.
  # Mutating ECS actions (UpdateService, RunTask) accept the cluster as the
  # resource — those are scoped below.
  statement {
    sid    = "ECSReadOnly"
    effect = "Allow"
    actions = [
      "ecs:DescribeClusters",
      "ecs:DescribeServices",
      "ecs:DescribeTasks",
      "ecs:DescribeTaskDefinition",
      "ecs:ListTasks",
      "ecs:ListServices",
      "ecs:ListTaskDefinitions",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ECSRegisterTaskDefinition"
    effect = "Allow"
    # RegisterTaskDefinition does NOT support resource-level constraints —
    # AWS evaluates iam:PassRole separately on the role ARNs in the request.
    actions   = ["ecs:RegisterTaskDefinition"]
    resources = ["*"]
  }

  statement {
    sid    = "ECSDeployToCluster"
    effect = "Allow"
    actions = [
      "ecs:UpdateService",
      "ecs:RunTask",
      "ecs:StopTask",
    ]
    resources = [
      var.cluster_arn,
      "${var.cluster_arn}/*",
      replace(var.cluster_arn, ":cluster/", ":service/"),
      "${replace(var.cluster_arn, ":cluster/", ":service/")}/*",
      replace(var.cluster_arn, ":cluster/", ":task-definition/"),
      "${replace(var.cluster_arn, ":cluster/", ":task-definition/")}/*",
      replace(var.cluster_arn, ":cluster/", ":task/"),
      "${replace(var.cluster_arn, ":cluster/", ":task/")}/*",
    ]
  }

  statement {
    sid       = "PassECSTaskRoles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = var.task_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "github_actions" {
  name   = "connect-github-actions-policy"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_inline.json
}
