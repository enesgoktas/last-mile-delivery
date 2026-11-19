variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "project_name" {
  type = string
}

variable "environment_name" {
  type = string
}

variable "application_metrics_namespace" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "ecs_subnet_ids" {
  type = list(string)
}

variable "ecs_assign_public_ip" {
  type = bool
}

variable "alb_security_group_id" {
  type = string
}

variable "alb_certificate_arn" {
  type = string

  validation {
    condition     = length(trimspace(var.alb_certificate_arn)) > 0
    error_message = "alb_certificate_arn must not be empty."
  }
}

variable "ecs_service_security_group_id" {
  type = string
}

variable "api_container_port" {
  type = number
}

variable "api_desired_count" {
  type = number
}

variable "worker_desired_count" {
  type = number
}

variable "api_image" {
  type = string
}

variable "worker_image" {
  type = string
}

variable "api_cpu" {
  type = number
}

variable "api_memory" {
  type = number
}

variable "worker_cpu" {
  type = number
}

variable "worker_memory" {
  type = number
}

variable "database_url" {
  type      = string
  sensitive = true
}

variable "redis_url" {
  type = string
}

variable "celery_broker_url" {
  type = string
}

variable "application_secret_arn" {
  type = string
}

variable "pod_bucket_name" {
  type = string
}

variable "pod_bucket_arn" {
  type = string
}

variable "dashboard_bucket_arn" {
  type = string
}

variable "tracking_bucket_arn" {
  type = string
}

variable "storage_kms_key_arn" {
  type = string

  validation {
    condition     = length(trimspace(var.storage_kms_key_arn)) > 0
    error_message = "storage_kms_key_arn must not be empty."
  }
}

variable "enable_blue_green" {
  type = bool
}

variable "blue_green_test_listener_port" {
  type = number
}

variable "codedeploy_termination_wait_minutes" {
  type = number
}

variable "codedeploy_deployment_config_name" {
  type = string
}

variable "codedeploy_alarm_names" {
  type = list(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}
