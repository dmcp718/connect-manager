output "sns_topic_arn" {
  description = "ARN of the connect-alerts SNS topic. Operators add additional subscriptions (PagerDuty, Slack webhook) out of band."
  value       = aws_sns_topic.connect_alerts.arn
}

output "alarm_arns" {
  description = "Map of alarm name → alarm ARN for every CloudWatch alarm created by this module. ALB-related alarms are present only when var.alb_arn_suffix is non-empty."
  value = merge(
    {
      (aws_cloudwatch_metric_alarm.rds_cpu_high.alarm_name)            = aws_cloudwatch_metric_alarm.rds_cpu_high.arn
      (aws_cloudwatch_metric_alarm.rds_free_storage_low.alarm_name)    = aws_cloudwatch_metric_alarm.rds_free_storage_low.arn
      (aws_cloudwatch_metric_alarm.elasticache_cpu_high.alarm_name)    = aws_cloudwatch_metric_alarm.elasticache_cpu_high.arn
      (aws_cloudwatch_metric_alarm.worker_queue_depth_high.alarm_name) = aws_cloudwatch_metric_alarm.worker_queue_depth_high.arn
      (aws_cloudwatch_metric_alarm.pod_restart_web.alarm_name)         = aws_cloudwatch_metric_alarm.pod_restart_web.arn
      (aws_cloudwatch_metric_alarm.pod_restart_worker.alarm_name)      = aws_cloudwatch_metric_alarm.pod_restart_worker.arn
    },
    local.alb_alarm_enabled ? {
      (aws_cloudwatch_metric_alarm.alb_5xx_rate[0].alarm_name) = aws_cloudwatch_metric_alarm.alb_5xx_rate[0].arn
    } : {}
  )
}
