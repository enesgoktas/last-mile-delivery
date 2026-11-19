resource "aws_cloudwatch_dashboard" "operations" {
  dashboard_name = "${var.name_prefix}-operations"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "ALB Request Rate / Target 5xx"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Sum"
          period = 60
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", var.alb_arn_suffix],
            [".", "HTTPCode_Target_5XX_Count", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "API ECS CPU / Memory"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Average"
          period = 60
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", var.ecs_cluster_name, "ServiceName", var.api_service_name],
            [".", "MemoryUtilization", ".", ".", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "RDS CPU"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Average"
          period = 60
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.db_instance_identifier],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Redis Memory Usage %"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Average"
          period = 60
          metrics = [
            ["AWS/ElastiCache", "DatabaseMemoryUsagePercentage", "ReplicationGroupId", var.redis_replication_group_id],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          title  = "Worker ECS CPU / Memory"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Average"
          period = 60
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", var.ecs_cluster_name, "ServiceName", var.worker_service_name],
            [".", "MemoryUtilization", ".", ".", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          title  = "RDS Connections / Redis Evictions"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Average"
          period = 60
          metrics = [
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", var.db_instance_identifier],
            ["AWS/ElastiCache", "Evictions", "ReplicationGroupId", var.redis_replication_group_id, { "stat" : "Sum" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          title  = "Application Latency p95"
          view   = "timeSeries"
          region = var.aws_region
          period = 300
          metrics = [
            [var.application_metrics_namespace, "dispatch_batch_latency_ms", "Application", var.project_name, "Environment", var.environment_name, "Service", var.api_metrics_service_label, { "stat" : "p95", "label" : "Dispatch batch p95" }],
            [".", "dispatch_on_demand_latency_ms", ".", ".", ".", ".", ".", ".", { "stat" : "p95", "label" : "Dispatch on-demand p95" }],
            [".", "eta_compute_latency_ms", ".", ".", ".", ".", ".", ".", { "stat" : "p95", "label" : "ETA compute p95" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          title  = "Webhook Delivery Outcomes"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Sum"
          period = 300
          metrics = [
            [var.application_metrics_namespace, "webhook_deliveries_enqueued", "Application", var.project_name, "Environment", var.environment_name, "Service", var.worker_metrics_service_label, { "label" : "Enqueued" }],
            [".", "webhook_delivery_success", ".", ".", ".", ".", ".", ".", { "label" : "Delivered" }],
            [".", "webhook_delivery_retry_scheduled", ".", ".", ".", ".", ".", ".", { "label" : "Retry scheduled" }],
            [".", "webhook_delivery_dead_letter", ".", ".", ".", ".", ".", ".", { "label" : "Dead-lettered" }],
          ]
        }
      },
    ]
  })
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx_high" {
  alarm_name          = "${var.name_prefix}-alb-5xx-high"
  alarm_description   = "ALB target 5xx errors above threshold"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "alb_latency_high" {
  alarm_name          = "${var.name_prefix}-alb-latency-high"
  alarm_description   = "ALB target response time elevated"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 2
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.api_target_group_arn_suffix
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "ecs_api_cpu_high" {
  alarm_name          = "${var.name_prefix}-ecs-api-cpu-high"
  alarm_description   = "API ECS service CPU is high"
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.api_service_name
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "ecs_worker_cpu_high" {
  alarm_name          = "${var.name_prefix}-ecs-worker-cpu-high"
  alarm_description   = "Worker ECS service CPU is high"
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.worker_service_name
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "ecs_worker_memory_high" {
  alarm_name          = "${var.name_prefix}-ecs-worker-memory-high"
  alarm_description   = "Worker ECS service memory is high"
  namespace           = "AWS/ECS"
  metric_name         = "MemoryUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.worker_service_name
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu_high" {
  alarm_name          = "${var.name_prefix}-rds-cpu-high"
  alarm_description   = "PostgreSQL CPU utilization is high"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    DBInstanceIdentifier = var.db_instance_identifier
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_connections_high" {
  alarm_name          = "${var.name_prefix}-rds-connections-high"
  alarm_description   = "PostgreSQL connection count is high"
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    DBInstanceIdentifier = var.db_instance_identifier
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "redis_memory_high" {
  alarm_name          = "${var.name_prefix}-redis-memory-high"
  alarm_description   = "Redis memory usage is high"
  namespace           = "AWS/ElastiCache"
  metric_name         = "DatabaseMemoryUsagePercentage"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    ReplicationGroupId = var.redis_replication_group_id
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "redis_evictions_high" {
  alarm_name          = "${var.name_prefix}-redis-evictions-high"
  alarm_description   = "Redis is evicting keys, indicating memory pressure"
  namespace           = "AWS/ElastiCache"
  metric_name         = "Evictions"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    ReplicationGroupId = var.redis_replication_group_id
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "alb_unhealthy_targets" {
  alarm_name          = "${var.name_prefix}-alb-unhealthy-targets"
  alarm_description   = "ALB target group has no healthy targets"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.api_target_group_arn_suffix
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "webhook_dead_letter_high" {
  alarm_name          = "${var.name_prefix}-webhook-dead-letter-high"
  alarm_description   = "Webhook dead-letter count indicates downstream delivery failures"
  namespace           = var.application_metrics_namespace
  metric_name         = "webhook_delivery_dead_letter"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arns
  ok_actions          = var.alarm_topic_arns

  dimensions = {
    Application = var.project_name
    Environment = var.environment_name
    Service     = var.worker_metrics_service_label
  }

  tags = var.tags
}
