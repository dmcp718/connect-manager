locals {
  tags = merge({ project = "connect", env = var.env }, var.tags)

  family       = "connect-${var.env}-worker"
  service_name = "connect-${var.env}-worker"
  log_group    = "/aws/ecs/connect-${var.env}/worker"

  capacity_provider = var.use_fargate_spot ? "FARGATE_SPOT" : "FARGATE"

  worker_environment = [
    for k, v in var.env_vars : { name = k, value = v }
  ]

  worker_secrets = [
    for k, v in var.secret_env_vars : { name = k, valueFrom = v }
  ]
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = local.log_group
  retention_in_days = var.log_retention_days

  tags = merge(local.tags, { Name = local.log_group })
}

# ── Task definition ──────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "worker" {
  family                   = local.family
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory

  execution_role_arn = var.execution_role_arn
  task_role_arn      = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = var.worker_image
      essential = true
      cpu       = var.worker_cpu
      memory    = var.worker_memory

      environment = local.worker_environment
      secrets     = local.worker_secrets

      stopTimeout = var.stop_timeout

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = local.log_group
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "worker"
        }
      }
    },
  ])

  tags = merge(local.tags, { Name = local.family })
}

# ── ECS service ──────────────────────────────────────────────────────────────

resource "aws_ecs_service" "worker" {
  name            = local.service_name
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.desired_count

  capacity_provider_strategy {
    capacity_provider = local.capacity_provider
    weight            = 1
    base              = 0
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200

  enable_execute_command = false

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = merge(local.tags, { Name = local.service_name })
}

# ── Auto Scaling: target tracking on custom queue-depth metric ───────────────

resource "aws_appautoscaling_target" "worker" {
  max_capacity       = var.max_count
  min_capacity       = var.min_count
  resource_id        = "service/${reverse(split("/", var.cluster_arn))[0]}/${local.service_name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"

  depends_on = [aws_ecs_service.worker]
}

resource "aws_appautoscaling_policy" "worker_queue" {
  name               = "${local.service_name}-queue-depth"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value       = var.target_queue_depth_per_worker
    scale_in_cooldown  = 300
    scale_out_cooldown = 30

    customized_metric_specification {
      metric_name = var.metric_name
      namespace   = var.metric_namespace
      statistic   = "Average"

      dimensions {
        name  = "Env"
        value = var.env
      }

      dimensions {
        name  = "Service"
        value = "worker"
      }
    }
  }
}
