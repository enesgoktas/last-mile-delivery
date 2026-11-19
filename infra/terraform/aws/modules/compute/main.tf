data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${var.name_prefix}-ecs-task-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_default" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name = "${var.name_prefix}-ecs-exec-secrets"
  role = aws_iam_role.ecs_task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "kms:Decrypt"]
        Resource = [var.application_secret_arn]
      }
    ]
  })
}

resource "aws_iam_role" "ecs_task_runtime" {
  name               = "${var.name_prefix}-ecs-task-runtime"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json

  tags = var.tags
}

resource "aws_iam_role_policy" "ecs_task_runtime" {
  name = "${var.name_prefix}-ecs-runtime-access"
  role = aws_iam_role.ecs_task_runtime.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = [
          "${var.pod_bucket_arn}/*",
          "${var.dashboard_bucket_arn}/*",
          "${var.tracking_bucket_arn}/*",
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:Encrypt",
          "kms:GenerateDataKey",
          "kms:DescribeKey",
        ]
        Resource = [var.storage_kms_key_arn]
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.name_prefix}/api"
  retention_in_days = 30

  tags = var.tags
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name_prefix}/worker"
  retention_in_days = 30

  tags = var.tags
}

resource "aws_ecs_cluster" "this" {
  name = "${var.name_prefix}-cluster"

  tags = var.tags
}

resource "aws_lb" "api" {
  name = substr(replace("${var.name_prefix}-alb", "_", "-"), 0, 32)
  #tfsec:ignore:aws-elb-alb-not-public Public API ingress is intentional.
  internal                   = false
  load_balancer_type         = "application"
  drop_invalid_header_fields = true
  security_groups            = [var.alb_security_group_id]
  subnets                    = var.public_subnet_ids

  tags = var.tags
}

resource "aws_lb_target_group" "api_blue" {
  name        = substr(replace("${var.name_prefix}-api-blue", "_", "-"), 0, 32)
  port        = var.api_container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
    path                = "/health"
  }

  tags = merge(var.tags, {
    Role = "blue"
  })
}

resource "aws_lb_target_group" "api_green" {
  name        = substr(replace("${var.name_prefix}-api-green", "_", "-"), 0, 32)
  port        = var.api_container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
    path                = "/health"
  }

  tags = merge(var.tags, {
    Role = "green"
  })
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.alb_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api_blue.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "test" {
  count = var.enable_blue_green ? 1 : 0

  load_balancer_arn = aws_lb.api.arn
  port              = var.blue_green_test_listener_port
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.alb_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api_green.arn
  }
}

locals {
  app_secret_jwt_ref     = "${var.application_secret_arn}:JWT_SECRET::"
  app_secret_webhook_ref = "${var.application_secret_arn}:WEBHOOK_SECRET_ENCRYPTION_KEY::"
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name_prefix}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.api_cpu)
  memory                   = tostring(var.api_memory)
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task_runtime.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = var.api_image
      essential = true
      portMappings = [
        {
          containerPort = var.api_container_port
          hostPort      = var.api_container_port
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "DATABASE_URL", value = var.database_url },
        { name = "REDIS_URL", value = var.redis_url },
        { name = "CELERY_BROKER_URL", value = var.celery_broker_url },
        { name = "CELERY_RESULT_BACKEND", value = var.celery_broker_url },
        { name = "S3_BUCKET", value = var.pod_bucket_name },
        { name = "ENVIRONMENT", value = var.environment_name },
        { name = "EMF_METRICS_ENABLED", value = "true" },
        { name = "EMF_METRICS_NAMESPACE", value = var.application_metrics_namespace },
        { name = "EMF_METRICS_APPLICATION", value = var.project_name },
        { name = "EMF_METRICS_SERVICE", value = "api" },
      ]
      secrets = [
        { name = "JWT_SECRET", valueFrom = local.app_secret_jwt_ref },
        { name = "WEBHOOK_SECRET_ENCRYPTION_KEY", valueFrom = local.app_secret_webhook_ref },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "api"
        }
      }
    }
  ])

  tags = var.tags
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name_prefix}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.worker_cpu)
  memory                   = tostring(var.worker_memory)
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task_runtime.arn

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = var.worker_image
      essential = true
      environment = [
        { name = "DATABASE_URL", value = var.database_url },
        { name = "REDIS_URL", value = var.redis_url },
        { name = "CELERY_BROKER_URL", value = var.celery_broker_url },
        { name = "CELERY_RESULT_BACKEND", value = var.celery_broker_url },
        { name = "S3_BUCKET", value = var.pod_bucket_name },
        { name = "ENVIRONMENT", value = var.environment_name },
        { name = "EMF_METRICS_ENABLED", value = "true" },
        { name = "EMF_METRICS_NAMESPACE", value = var.application_metrics_namespace },
        { name = "EMF_METRICS_APPLICATION", value = var.project_name },
        { name = "EMF_METRICS_SERVICE", value = "worker" },
      ]
      secrets = [
        { name = "JWT_SECRET", valueFrom = local.app_secret_jwt_ref },
        { name = "WEBHOOK_SECRET_ENCRYPTION_KEY", valueFrom = local.app_secret_webhook_ref },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.worker.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "worker"
        }
      }
    }
  ])

  tags = var.tags
}

resource "aws_ecs_service" "api" {
  name            = "${var.name_prefix}-api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  deployment_controller {
    type = var.enable_blue_green ? "CODE_DEPLOY" : "ECS"
  }

  network_configuration {
    assign_public_ip = var.ecs_assign_public_ip
    subnets          = var.ecs_subnet_ids
    security_groups  = [var.ecs_service_security_group_id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api_blue.arn
    container_name   = "api"
    container_port   = var.api_container_port
  }

  depends_on = [aws_lb_listener.https]

  tags = var.tags
}

resource "aws_ecs_service" "worker" {
  name            = "${var.name_prefix}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    assign_public_ip = var.ecs_assign_public_ip
    subnets          = var.ecs_subnet_ids
    security_groups  = [var.ecs_service_security_group_id]
  }

  tags = var.tags
}

data "aws_iam_policy_document" "codedeploy_assume_role" {
  count = var.enable_blue_green ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codedeploy.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "codedeploy" {
  count = var.enable_blue_green ? 1 : 0

  name               = "${var.name_prefix}-codedeploy-ecs"
  assume_role_policy = data.aws_iam_policy_document.codedeploy_assume_role[0].json

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "codedeploy_ecs" {
  count = var.enable_blue_green ? 1 : 0

  role       = aws_iam_role.codedeploy[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSCodeDeployRoleForECS"
}

resource "aws_codedeploy_app" "api" {
  count = var.enable_blue_green ? 1 : 0

  compute_platform = "ECS"
  name             = "${var.name_prefix}-api"

  tags = var.tags
}

resource "aws_codedeploy_deployment_group" "api" {
  count = var.enable_blue_green ? 1 : 0

  app_name               = aws_codedeploy_app.api[0].name
  deployment_group_name  = "${var.name_prefix}-api"
  service_role_arn       = aws_iam_role.codedeploy[0].arn
  deployment_config_name = var.codedeploy_deployment_config_name

  deployment_style {
    deployment_option = "WITH_TRAFFIC_CONTROL"
    deployment_type   = "BLUE_GREEN"
  }

  auto_rollback_configuration {
    enabled = true
    events = [
      "DEPLOYMENT_FAILURE",
      "DEPLOYMENT_STOP_ON_ALARM",
      "DEPLOYMENT_STOP_ON_REQUEST",
    ]
  }

  blue_green_deployment_config {
    deployment_ready_option {
      action_on_timeout = "CONTINUE_DEPLOYMENT"
    }

    terminate_blue_instances_on_deployment_success {
      action                           = "TERMINATE"
      termination_wait_time_in_minutes = var.codedeploy_termination_wait_minutes
    }
  }

  dynamic "alarm_configuration" {
    for_each = length(var.codedeploy_alarm_names) > 0 ? [1] : []
    content {
      enabled                   = true
      alarms                    = var.codedeploy_alarm_names
      ignore_poll_alarm_failure = false
    }
  }

  ecs_service {
    cluster_name = aws_ecs_cluster.this.name
    service_name = aws_ecs_service.api.name
  }

  load_balancer_info {
    target_group_pair_info {
      prod_traffic_route {
        listener_arns = [aws_lb_listener.https.arn]
      }

      test_traffic_route {
        listener_arns = [aws_lb_listener.test[0].arn]
      }

      target_group {
        name = aws_lb_target_group.api_blue.name
      }

      target_group {
        name = aws_lb_target_group.api_green.name
      }
    }
  }

  depends_on = [aws_iam_role_policy_attachment.codedeploy_ecs]
}
