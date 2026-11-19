output "required_alarm_names" {
  value = [
    aws_cloudwatch_metric_alarm.alb_unhealthy_targets.alarm_name,
    aws_cloudwatch_metric_alarm.ecs_worker_cpu_high.alarm_name,
    aws_cloudwatch_metric_alarm.ecs_worker_memory_high.alarm_name,
    aws_cloudwatch_metric_alarm.rds_connections_high.alarm_name,
    aws_cloudwatch_metric_alarm.redis_evictions_high.alarm_name,
    aws_cloudwatch_metric_alarm.webhook_dead_letter_high.alarm_name,
  ]
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.operations.dashboard_name
}
