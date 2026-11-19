locals {
  db_url = format(
    "postgresql+asyncpg://%s:%s@%s:%d/%s",
    var.db_username,
    var.db_password,
    module.data.db_endpoint,
    module.data.db_port,
    var.db_name,
  )

  app_redis_url     = format("redis://%s:%d/%d", module.data.redis_primary_endpoint, module.data.redis_port, var.redis_app_db_index)
  celery_broker_url = format("redis://%s:%d/%d", module.data.redis_primary_endpoint, module.data.redis_port, var.celery_broker_db_index)
}

module "compute" {
  source = "./modules/compute"

  name_prefix                         = local.name_prefix
  aws_region                          = var.aws_region
  project_name                        = var.project_name
  environment_name                    = var.environment
  application_metrics_namespace       = var.application_metrics_namespace
  vpc_id                              = module.network.vpc_id
  public_subnet_ids                   = module.network.public_subnet_ids
  ecs_subnet_ids                      = module.network.ecs_subnet_ids
  ecs_assign_public_ip                = module.network.ecs_assign_public_ip
  alb_security_group_id               = module.security.alb_security_group_id
  alb_certificate_arn                 = var.alb_certificate_arn
  ecs_service_security_group_id       = module.security.ecs_service_security_group_id
  api_container_port                  = var.api_container_port
  api_desired_count                   = var.api_desired_count
  worker_desired_count                = var.worker_desired_count
  api_image                           = var.api_image
  worker_image                        = var.worker_image
  api_cpu                             = var.api_cpu
  api_memory                          = var.api_memory
  worker_cpu                          = var.worker_cpu
  worker_memory                       = var.worker_memory
  database_url                        = local.db_url
  redis_url                           = local.app_redis_url
  celery_broker_url                   = local.celery_broker_url
  application_secret_arn              = module.data.application_secret_arn
  pod_bucket_name                     = module.storage.pod_bucket_name
  pod_bucket_arn                      = module.storage.pod_bucket_arn
  dashboard_bucket_arn                = module.storage.dashboard_bucket_arn
  tracking_bucket_arn                 = module.storage.tracking_bucket_arn
  storage_kms_key_arn                 = module.storage.storage_kms_key_arn
  enable_blue_green                   = var.enable_blue_green
  blue_green_test_listener_port       = var.blue_green_test_listener_port
  codedeploy_termination_wait_minutes = var.codedeploy_termination_wait_minutes
  codedeploy_deployment_config_name   = var.codedeploy_deployment_config_name
  codedeploy_alarm_names              = var.codedeploy_alarm_names
  tags                                = var.tags
}
