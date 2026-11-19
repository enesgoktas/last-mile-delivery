output "db_endpoint" {
  value = aws_db_instance.postgres.address
}

output "db_port" {
  value = aws_db_instance.postgres.port
}

output "db_instance_identifier" {
  value = aws_db_instance.postgres.identifier
}

output "redis_primary_endpoint" {
  value = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "redis_port" {
  value = aws_elasticache_replication_group.redis.port
}

output "redis_replication_group_id" {
  value = aws_elasticache_replication_group.redis.id
}

output "application_secret_arn" {
  value = aws_secretsmanager_secret.application.arn
}
