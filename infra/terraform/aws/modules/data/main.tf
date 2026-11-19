resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-db-subnets"
  subnet_ids = var.private_subnet_ids

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-db-subnets"
  })
}

resource "aws_db_instance" "postgres" {
  identifier = "${var.name_prefix}-postgres"

  engine         = "postgres"
  engine_version = "16.3"
  instance_class = var.db_instance_class

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  allocated_storage       = var.db_allocated_storage
  max_allocated_storage   = var.db_allocated_storage * 2
  storage_encrypted       = true
  backup_retention_period = var.db_backup_retention_days

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.rds_security_group_id]
  publicly_accessible    = false
  multi_az               = var.environment == "production"
  deletion_protection    = var.environment == "production"

  skip_final_snapshot = var.db_skip_final_snapshot

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-postgres"
  })
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name_prefix}-redis-subnets"
  subnet_ids = var.private_subnet_ids
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = replace("${var.name_prefix}-redis", "_", "-")
  description                = "Redis cache for Last-Mile Delivery"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  num_cache_clusters         = var.redis_num_cache_clusters
  automatic_failover_enabled = var.redis_num_cache_clusters > 1
  parameter_group_name       = "default.redis7"
  port                       = 6379

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [var.redis_security_group_id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-redis"
  })
}

resource "aws_secretsmanager_secret" "application" {
  name        = "${var.name_prefix}/application"
  description = "Runtime secrets for Last-Mile backend services"

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-application-secrets"
  })
}

resource "aws_secretsmanager_secret_version" "application" {
  secret_id = aws_secretsmanager_secret.application.id
  secret_string = jsonencode({
    JWT_SECRET                    = var.jwt_secret != "" ? var.jwt_secret : "change-me-jwt-secret"
    WEBHOOK_SECRET_ENCRYPTION_KEY = var.webhook_secret_encryption_key != "" ? var.webhook_secret_encryption_key : "change-me-webhook-secret"
  })
}
