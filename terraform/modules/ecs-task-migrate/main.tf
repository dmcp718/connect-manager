locals {
  tags = merge({ project = "connect", env = var.env }, var.tags)

  family    = "connect-${var.env}-migrate"
  log_group = "/aws/ecs/connect-${var.env}/migrate"

  environment = [
    for k, v in var.env_vars : { name = k, value = v }
  ]

  secrets = [
    for k, v in var.secret_env_vars : { name = k, valueFrom = v }
  ]
}

resource "aws_cloudwatch_log_group" "migrate" {
  name              = local.log_group
  retention_in_days = var.log_retention_days

  tags = merge(local.tags, { Name = local.log_group })
}

resource "aws_ecs_task_definition" "migrate" {
  family                   = local.family
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.cpu
  memory                   = var.memory

  execution_role_arn = var.execution_role_arn
  task_role_arn      = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "migrate"
      image     = var.web_image
      essential = true
      cpu       = var.cpu
      memory    = var.memory

      command = var.command

      environment = local.environment
      secrets     = local.secrets

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = local.log_group
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "migrate"
        }
      }
    },
  ])

  tags = merge(local.tags, { Name = local.family })
}
