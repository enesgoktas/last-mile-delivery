output "aws_region" {
  value = var.aws_region
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "public_subnet_ids" {
  value = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "alb_dns_name" {
  value = module.compute.alb_dns_name
}

output "ecs_cluster_name" {
  value = module.compute.ecs_cluster_name
}

output "api_service_name" {
  value = module.compute.api_service_name
}

output "worker_service_name" {
  value = module.compute.worker_service_name
}

output "rds_endpoint" {
  value = module.data.db_endpoint
}

output "redis_primary_endpoint" {
  value = module.data.redis_primary_endpoint
}

output "dashboard_bucket_name" {
  value = module.storage.dashboard_bucket_name
}

output "tracking_bucket_name" {
  value = module.storage.tracking_bucket_name
}

output "pod_bucket_name" {
  value = module.storage.pod_bucket_name
}

output "application_secret_arn" {
  value = module.data.application_secret_arn
}

output "api_ecr_repository_url" {
  value = module.storage.api_ecr_repository_url
}

output "worker_ecr_repository_url" {
  value = module.storage.worker_ecr_repository_url
}

output "codedeploy_api_application_name" {
  value = module.compute.api_codedeploy_app_name
}

output "codedeploy_api_deployment_group_name" {
  value = module.compute.api_codedeploy_deployment_group_name
}

output "api_task_definition_family" {
  value = module.compute.api_task_definition_family
}

output "worker_task_definition_family" {
  value = module.compute.worker_task_definition_family
}

output "required_alarm_names" {
  value = module.observability.required_alarm_names
}
