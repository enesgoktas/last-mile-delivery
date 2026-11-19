module "observability" {
  source = "./modules/observability"

  name_prefix                 = local.name_prefix
  aws_region                  = var.aws_region
  project_name                = var.project_name
  environment_name            = var.environment
  application_metrics_namespace = var.application_metrics_namespace
  alarm_topic_arns            = var.alarm_topic_arns
  alb_arn_suffix              = module.compute.alb_arn_suffix
  api_target_group_arn_suffix = module.compute.api_target_group_arn_suffix
  ecs_cluster_name            = module.compute.ecs_cluster_name
  api_service_name            = module.compute.api_service_name
  worker_service_name         = module.compute.worker_service_name
  db_instance_identifier      = module.data.db_instance_identifier
  redis_replication_group_id  = module.data.redis_replication_group_id
  tags                        = var.tags
}
