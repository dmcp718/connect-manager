locals {
  tags = merge({ project = "connect", env = var.env }, var.tags)

  family           = "connect-${var.env}-web"
  service_name     = "connect-${var.env}-web"
  web_log_group    = "/aws/ecs/connect-${var.env}/web"
  ll_api_log_group = "/aws/ecs/connect-${var.env}/lucidlink-api"

  task_cpu    = var.web_cpu + var.lucidlink_api_cpu
  task_memory = var.web_memory + var.lucidlink_api_memory

  capacity_provider = var.use_fargate_spot ? "FARGATE_SPOT" : "FARGATE"

  web_environment = [
    for k, v in var.env_vars : { name = k, value = v }
  ]

  web_secrets = [
    for k, v in var.secret_env_vars : { name = k, valueFrom = v }
  ]
}

# ── Log groups ───────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "web" {
  name              = local.web_log_group
  retention_in_days = var.log_retention_days

  tags = merge(local.tags, { Name = local.web_log_group })
}

resource "aws_cloudwatch_log_group" "lucidlink_api" {
  name              = local.ll_api_log_group
  retention_in_days = var.log_retention_days

  tags = merge(local.tags, { Name = local.ll_api_log_group })
}

# ── Task definition: web + lucidlink-api sidecar ─────────────────────────────

resource "aws_ecs_task_definition" "web" {
  family                   = local.family
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = local.task_cpu
  memory                   = local.task_memory

  execution_role_arn = var.execution_role_arn
  task_role_arn      = var.task_role_arn

  container_definitions = jsonencode([
    {
      name      = "lucidlink-api"
      image     = var.lucidlink_api_image
      essential = true
      cpu       = var.lucidlink_api_cpu
      memory    = var.lucidlink_api_memory

      portMappings = [
        {
          containerPort = var.lucidlink_api_port
          protocol      = "tcp"
        }
      ]

      healthCheck = {
        # The lucidlink-api image is a Nest.js app on Node.js; bash isn't
        # guaranteed in the image so /dev/tcp/... redirections won't work.
        # Use node (always present) to GET the root and pass on any HTTP
        # response under 500.
        command = [
          "CMD-SHELL",
          "node -e \"require('http').get('http://127.0.0.1:${var.lucidlink_api_port}/', r => process.exit(r.statusCode<500?0:1)).on('error', () => process.exit(1))\"",
        ]
        interval    = 15
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = local.ll_api_log_group
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "lucidlink-api"
        }
      }
    },
    {
      name      = "web"
      image     = var.web_image
      essential = true
      cpu       = var.web_cpu
      memory    = var.web_memory

      portMappings = [
        {
          containerPort = var.web_port
          protocol      = "tcp"
        }
      ]

      environment = local.web_environment
      secrets     = local.web_secrets

      dependsOn = [
        {
          containerName = "lucidlink-api"
          # START rather than HEALTHY: web does its own retries when calling
          # the sidecar. Waiting for HEALTHY adds 30s+ of startup latency
          # without preventing the few transient failures during the
          # sidecar's own warm-up.
          condition = "START"
        }
      ]

      stopTimeout = var.stop_timeout

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = local.web_log_group
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "web"
        }
      }
    },
  ])

  tags = merge(local.tags, { Name = local.family })
}

# ── ECS service ──────────────────────────────────────────────────────────────

resource "aws_ecs_service" "web" {
  name            = local.service_name
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.desired_count

  capacity_provider_strategy {
    capacity_provider = local.capacity_provider
    weight            = 1
    base              = var.desired_count
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "web"
    container_port   = var.web_port
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  enable_execute_command            = false
  health_check_grace_period_seconds = 60

  # Auto Scaling owns desired_count after the initial create.
  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = merge(local.tags, { Name = local.service_name })
}

# ── Auto Scaling: target tracking on CPU ─────────────────────────────────────

resource "aws_appautoscaling_target" "web" {
  max_capacity       = var.max_count
  min_capacity       = var.min_count
  resource_id        = "service/${reverse(split("/", var.cluster_arn))[0]}/${local.service_name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"

  depends_on = [aws_ecs_service.web]
}

resource "aws_appautoscaling_policy" "web_cpu" {
  name               = "${local.service_name}-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.web.resource_id
  scalable_dimension = aws_appautoscaling_target.web.scalable_dimension
  service_namespace  = aws_appautoscaling_target.web.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value       = var.target_cpu_utilization
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
