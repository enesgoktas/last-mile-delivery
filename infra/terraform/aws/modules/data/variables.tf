variable "name_prefix" {
  type = string
}

variable "environment" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "rds_security_group_id" {
  type = string
}

variable "redis_security_group_id" {
  type = string
}

variable "db_name" {
  type = string
}

variable "db_username" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "db_instance_class" {
  type = string
}

variable "db_allocated_storage" {
  type = number
}

variable "db_backup_retention_days" {
  type = number
}

variable "db_skip_final_snapshot" {
  type = bool
}

variable "redis_node_type" {
  type = string
}

variable "redis_num_cache_clusters" {
  type = number
}

variable "jwt_secret" {
  type      = string
  sensitive = true
}

variable "webhook_secret_encryption_key" {
  type      = string
  sensitive = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
