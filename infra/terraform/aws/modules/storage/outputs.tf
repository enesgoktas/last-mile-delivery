output "dashboard_bucket_name" {
  value = aws_s3_bucket.dashboard.id
}

output "tracking_bucket_name" {
  value = aws_s3_bucket.tracking.id
}

output "pod_bucket_name" {
  value = aws_s3_bucket.pod_assets.id
}

output "dashboard_bucket_arn" {
  value = aws_s3_bucket.dashboard.arn
}

output "tracking_bucket_arn" {
  value = aws_s3_bucket.tracking.arn
}

output "pod_bucket_arn" {
  value = aws_s3_bucket.pod_assets.arn
}

output "api_ecr_repository_name" {
  value = aws_ecr_repository.api.name
}

output "worker_ecr_repository_name" {
  value = aws_ecr_repository.worker.name
}

output "api_ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "worker_ecr_repository_url" {
  value = aws_ecr_repository.worker.repository_url
}

output "storage_kms_key_arn" {
  value = aws_kms_key.storage.arn
}
