variable "project_name" {
  type        = string
  description = "Project slug used for naming resources."
  default     = "last-mile-delivery"
}

variable "environment" {
  type        = string
  description = "Deployment environment name."
  default     = "staging"

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be one of: dev, staging, production."
  }
}

variable "aws_region" {
  type        = string
  description = "AWS region for resource provisioning."
  default     = "us-east-1"
}

variable "application_metrics_namespace" {
  type        = string
  description = "CloudWatch namespace used for application runtime metrics emitted via EMF."
  default     = "LastMile/Application"
}

variable "availability_zones" {
  type        = list(string)
  description = "Optional AZ override. If empty, available AZs in region are used."
  default     = []
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR block for VPC."
  default     = "10.40.0.0/16"
}

variable "public_subnet_cidrs" {
  type        = list(string)
  description = "Public subnet CIDR blocks."
  default     = ["10.40.0.0/24", "10.40.1.0/24"]
}

variable "private_subnet_cidrs" {
  type        = list(string)
  description = "Private subnet CIDR blocks."
  default     = ["10.40.10.0/24", "10.40.11.0/24"]
}

variable "allowed_ingress_cidrs" {
  type        = list(string)
  description = "CIDR blocks allowed to reach ALB."
  default     = ["0.0.0.0/0"]
}

variable "alb_certificate_arn" {
  type        = string
  description = "ACM certificate ARN used by ALB HTTPS listeners."

  validation {
    condition     = can(regex("^arn:aws[a-z-]*:acm:[a-z0-9-]+:[0-9]{12}:certificate/.+$", var.alb_certificate_arn))
    error_message = "alb_certificate_arn must be a valid ACM certificate ARN."
  }
}

variable "enable_nat_gateway" {
  type        = bool
  description = "Whether to provision a NAT gateway for private subnets."
  default     = true
}

variable "s3_force_destroy" {
  type        = bool
  description = "Allow deleting non-empty S3 buckets."
  default     = false
}

variable "api_container_port" {
  type        = number
  description = "FastAPI container port."
  default     = 8000
}

variable "api_desired_count" {
  type        = number
  description = "Desired count for API ECS service."
  default     = 2
}

variable "worker_desired_count" {
  type        = number
  description = "Desired count for worker ECS service."
  default     = 1
}

variable "api_image" {
  type        = string
  description = "Container image for API task definition."
  default     = "public.ecr.aws/docker/library/nginx:stable-alpine"
}

variable "worker_image" {
  type        = string
  description = "Container image for worker task definition."
  default     = "public.ecr.aws/docker/library/nginx:stable-alpine"
}

variable "api_cpu" {
  type        = number
  description = "API task CPU units."
  default     = 512
}

variable "api_memory" {
  type        = number
  description = "API task memory (MiB)."
  default     = 1024
}

variable "worker_cpu" {
  type        = number
  description = "Worker task CPU units."
  default     = 512
}

variable "worker_memory" {
  type        = number
  description = "Worker task memory (MiB)."
  default     = 1024
}

variable "db_name" {
  type        = string
  description = "PostgreSQL database name."
  default     = "delivery_db"
}

variable "db_username" {
  type        = string
  description = "PostgreSQL username."
  default     = "delivery"
}

variable "db_password" {
  type        = string
  description = "PostgreSQL password."
  sensitive   = true
}

variable "db_instance_class" {
  type        = string
  description = "RDS PostgreSQL instance class."
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type        = number
  description = "Initial RDS storage in GB."
  default     = 50
}

variable "db_backup_retention_days" {
  type        = number
  description = "Days to retain automated DB backups."
  default     = 7
}

variable "db_skip_final_snapshot" {
  type        = bool
  description = "Skip final snapshot on DB destroy."
  default     = true
}

variable "redis_node_type" {
  type        = string
  description = "ElastiCache node type."
  default     = "cache.t4g.micro"
}

variable "redis_num_cache_clusters" {
  type        = number
  description = "Number of cache nodes in replication group."
  default     = 1
}

variable "redis_app_db_index" {
  type        = number
  description = "Redis database index for app cache."
  default     = 0
}

variable "celery_broker_db_index" {
  type        = number
  description = "Redis database index for Celery broker/result backend."
  default     = 1
}

variable "jwt_secret" {
  type        = string
  description = "JWT secret value for application secret manager entry."
  default     = ""
  sensitive   = true
}

variable "webhook_secret_encryption_key" {
  type        = string
  description = "Webhook secret encryption key for application secret manager entry."
  default     = ""
  sensitive   = true
}

variable "alarm_topic_arns" {
  type        = list(string)
  description = "SNS topic ARNs for CloudWatch alarms."
  default     = []
}

variable "enable_cost_budget" {
  type        = bool
  description = "Enable AWS Budget resource for monthly spend guardrails."
  default     = true
}

variable "monthly_cost_budget_usd" {
  type        = number
  description = "Monthly AWS cost budget threshold in USD."
  default     = 350
}

variable "budget_time_period_start" {
  type        = string
  description = "Budget start timestamp in AWS Budgets format (YYYY-MM-DD_hh:mm)."
  default     = "2026-01-01_00:00"
}

variable "budget_actual_threshold_percent" {
  type        = number
  description = "Alert threshold percentage for ACTUAL spend budget notifications."
  default     = 80
}

variable "budget_forecast_threshold_percent" {
  type        = number
  description = "Alert threshold percentage for FORECASTED spend budget notifications."
  default     = 100
}

variable "budget_notification_emails" {
  type        = list(string)
  description = "Email recipients for budget alerts."
  default     = []
}

variable "budget_notification_topic_arns" {
  type        = list(string)
  description = "SNS topic ARNs for budget alerts."
  default     = []
}

variable "enable_blue_green" {
  type        = bool
  description = "Enable ECS blue/green deployment topology and CodeDeploy resources."
  default     = true
}

variable "blue_green_test_listener_port" {
  type        = number
  description = "ALB test listener port used by CodeDeploy test traffic route."
  default     = 9002
}

variable "codedeploy_termination_wait_minutes" {
  type        = number
  description = "How long CodeDeploy keeps old task set before termination."
  default     = 10
}

variable "codedeploy_deployment_config_name" {
  type        = string
  description = "CodeDeploy deployment configuration name for ECS blue/green."
  default     = "CodeDeployDefault.ECSAllAtOnce"
}

variable "codedeploy_alarm_names" {
  type        = list(string)
  description = "CloudWatch alarms CodeDeploy monitors for auto rollback."
  default     = []
}

variable "tags" {
  type        = map(string)
  description = "Additional tags to apply to all resources."
  default     = {}
}
