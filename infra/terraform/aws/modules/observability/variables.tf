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

variable "api_metrics_service_label" {
  type    = string
  default = "api"
}

variable "worker_metrics_service_label" {
  type    = string
  default = "worker"
}

variable "alarm_topic_arns" {
  type = list(string)
}

variable "alb_arn_suffix" {
  type = string
}

variable "api_target_group_arn_suffix" {
  type = string
}

variable "ecs_cluster_name" {
  type = string
}

variable "api_service_name" {
  type = string
}

variable "worker_service_name" {
  type = string
}

variable "db_instance_identifier" {
  type = string
}

variable "redis_replication_group_id" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
