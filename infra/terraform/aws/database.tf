module "data" {
  source = "./modules/data"

  name_prefix                   = local.name_prefix
  environment                   = var.environment
  private_subnet_ids            = module.network.private_subnet_ids
  rds_security_group_id         = module.security.rds_security_group_id
  redis_security_group_id       = module.security.redis_security_group_id
  db_name                       = var.db_name
  db_username                   = var.db_username
  db_password                   = var.db_password
  db_instance_class             = var.db_instance_class
  db_allocated_storage          = var.db_allocated_storage
  db_backup_retention_days      = var.db_backup_retention_days
  db_skip_final_snapshot        = var.db_skip_final_snapshot
  redis_node_type               = var.redis_node_type
  redis_num_cache_clusters      = var.redis_num_cache_clusters
  jwt_secret                    = var.jwt_secret
  webhook_secret_encryption_key = var.webhook_secret_encryption_key
  tags                          = var.tags
}
