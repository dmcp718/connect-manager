locals {
  tags = merge({ project = "connect" }, var.tags)
}

# ---------------------------------------------------------------------------
# SNS topic — single fan-out point for every alarm in this module.
# Subscriptions (email/PagerDuty/Slack) are operator-configured out of band;
# only the email subscription is wired here as a convenience.
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "connect_alerts" {
  name = "connect-alerts"

  tags = merge(local.tags, { Name = "connect-alerts" })
}

resource "aws_sns_topic_subscription" "email" {
  count = var.sns_topic_subscription_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.connect_alerts.arn
  protocol  = "email"
  endpoint  = var.sns_topic_subscription_email
}

# ---------------------------------------------------------------------------
# ALB 5xx error rate > 1% for 5m — SPEC §8.3
#
# CloudWatch can't compute a percentage from a single metric, so we use a
# math expression: e1 = (m1 / m2) * 100 where m1 = HTTPCode_Target_5XX_Count
# and m2 = RequestCount. The alarm triggers on e1 > 1.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "alb_5xx_rate" {
  count = var.create_alb_alarm ? 1 : 0

  alarm_name          = "connect-alb-5xx-rate"
  alarm_description   = "ALB target 5xx error rate exceeded 1% over 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 1
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "e1"
    expression  = "(m1 / m2) * 100"
    label       = "5xx error rate (%)"
    return_data = true
  }

  metric_query {
    id          = "m1"
    return_data = false
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      period      = 300
      stat        = "Sum"
      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  metric_query {
    id          = "m2"
    return_data = false
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      period      = 300
      stat        = "Sum"
      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  alarm_actions = [aws_sns_topic.connect_alerts.arn]
  ok_actions    = [aws_sns_topic.connect_alerts.arn]

  tags = merge(local.tags, { Name = "connect-alb-5xx-rate" })
}

# ---------------------------------------------------------------------------
# RDS CPU > 80% for 10m — SPEC §8.3
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "rds_cpu_high" {
  alarm_name          = "connect-rds-cpu-high"
  alarm_description   = "RDS CPUUtilization exceeded 80% for 10 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = var.db_instance_id
  }

  alarm_actions = [aws_sns_topic.connect_alerts.arn]
  ok_actions    = [aws_sns_topic.connect_alerts.arn]

  tags = merge(local.tags, { Name = "connect-rds-cpu-high" })
}

# ---------------------------------------------------------------------------
# RDS free storage < 5GB — SPEC §8.3
# FreeStorageSpace is reported in bytes; 5 GiB = 5 * 1024^3.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "rds_free_storage_low" {
  alarm_name          = "connect-rds-free-storage-low"
  alarm_description   = "RDS FreeStorageSpace dropped below 5 GiB."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 5 * 1024 * 1024 * 1024
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = var.db_instance_id
  }

  alarm_actions = [aws_sns_topic.connect_alerts.arn]
  ok_actions    = [aws_sns_topic.connect_alerts.arn]

  tags = merge(local.tags, { Name = "connect-rds-free-storage-low" })
}

# ---------------------------------------------------------------------------
# ElastiCache CPU > 75% for 10m — SPEC §8.3
# EngineCPUUtilization is the per-engine-thread metric — more accurate for
# single-threaded Redis/Valkey than the overall CPUUtilization on multi-core
# nodes, but CPUUtilization is what SPEC §8.3 names. Use CPUUtilization for
# strict spec match.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "elasticache_cpu_high" {
  alarm_name          = "connect-elasticache-cpu-high"
  alarm_description   = "ElastiCache CPUUtilization exceeded 75% for 10 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 75
  treat_missing_data  = "notBreaching"

  dimensions = {
    ReplicationGroupId = var.cache_cluster_id
  }

  alarm_actions = [aws_sns_topic.connect_alerts.arn]
  ok_actions    = [aws_sns_topic.connect_alerts.arn]

  tags = merge(local.tags, { Name = "connect-elasticache-cpu-high" })
}

# ---------------------------------------------------------------------------
# Worker queue depth > 100 for 15m — SPEC §8.3
#
# This alarm reads the `queue_depth` metric in the `Connect/ARQ` namespace,
# which is the CloudWatch destination for the `connect_arq_queue_depth`
# Prometheus gauge exposed by the worker. The Prometheus → CloudWatch bridge
# (ADOT collector or CloudWatch Agent with prometheus_exporter scrape config)
# is a separate operational concern — see README.md.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "worker_queue_depth_high" {
  alarm_name          = "connect-worker-queue-depth-high"
  alarm_description   = "ARQ queue depth exceeded 100 for 15 minutes (KEDA isn't keeping up)."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "queue_depth"
  namespace           = var.prometheus_namespace
  period              = 300
  statistic           = "Maximum"
  threshold           = 100
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.connect_alerts.arn]
  ok_actions    = [aws_sns_topic.connect_alerts.arn]

  tags = merge(local.tags, { Name = "connect-worker-queue-depth-high" })
}

# ---------------------------------------------------------------------------
# Pod restart rate > 5/h on web — SPEC §8.3
#
# Container Insights publishes `pod_number_of_container_restarts` as a
# cumulative count per pod. Using `Sum` over a 1-hour period across pods in
# the connect namespace approximates total restarts/hour for the deployment.
# A separate alarm is defined per component so the alert message identifies
# which one is flapping.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "pod_restart_web" {
  alarm_name          = "connect-pod-restart-web"
  alarm_description   = "connect-web pod restart count exceeded 5 per hour."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "pod_number_of_container_restarts"
  namespace           = "ContainerInsights"
  period              = 3600
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = var.cluster_name
    Namespace   = var.namespace
    Service     = "connect-web"
  }

  alarm_actions = [aws_sns_topic.connect_alerts.arn]
  ok_actions    = [aws_sns_topic.connect_alerts.arn]

  tags = merge(local.tags, { Name = "connect-pod-restart-web" })
}

resource "aws_cloudwatch_metric_alarm" "pod_restart_worker" {
  alarm_name          = "connect-pod-restart-worker"
  alarm_description   = "connect-worker pod restart count exceeded 5 per hour."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "pod_number_of_container_restarts"
  namespace           = "ContainerInsights"
  period              = 3600
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = var.cluster_name
    Namespace   = var.namespace
    Service     = "connect-worker"
  }

  alarm_actions = [aws_sns_topic.connect_alerts.arn]
  ok_actions    = [aws_sns_topic.connect_alerts.arn]

  tags = merge(local.tags, { Name = "connect-pod-restart-worker" })
}
