resource "aws_s3_bucket" "dashboard" {
  bucket_prefix = "${var.name_prefix}-dashboard-"
  force_destroy = var.s3_force_destroy

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-dashboard"
  })
}

resource "aws_s3_bucket" "tracking" {
  bucket_prefix = "${var.name_prefix}-tracking-"
  force_destroy = var.s3_force_destroy

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-tracking"
  })
}

resource "aws_s3_bucket" "pod_assets" {
  bucket_prefix = "${var.name_prefix}-pod-"
  force_destroy = var.s3_force_destroy

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-pod"
  })
}

resource "aws_kms_key" "storage" {
  description             = "KMS key for ${var.name_prefix} application bucket encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-storage-kms"
  })
}

resource "aws_kms_alias" "storage" {
  name          = "alias/${replace(var.name_prefix, "_", "-")}-storage"
  target_key_id = aws_kms_key.storage.key_id
}

resource "aws_s3_bucket_versioning" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_versioning" "tracking" {
  bucket = aws_s3_bucket.tracking.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_versioning" "pod_assets" {
  bucket = aws_s3_bucket.pod_assets.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.storage.arn
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tracking" {
  bucket = aws_s3_bucket.tracking.id

  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.storage.arn
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pod_assets" {
  bucket = aws_s3_bucket.pod_assets.id

  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.storage.arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "tracking" {
  bucket = aws_s3_bucket.tracking.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "pod_assets" {
  bucket = aws_s3_bucket.pod_assets.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "pod_assets" {
  bucket = aws_s3_bucket.pod_assets.id

  rule {
    id     = "pod-intelligent-tiering"
    status = "Enabled"
    filter {}

    transition {
      days          = 30
      storage_class = "INTELLIGENT_TIERING"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

resource "aws_ecr_repository" "api" {
  name                 = "${var.name_prefix}/backend-api"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-backend-api"
  })
}

resource "aws_ecr_repository" "worker" {
  name                 = "${var.name_prefix}/backend-worker"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-backend-worker"
  })
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep latest 25 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 25
        }
        action = { type = "expire" }
      }
    ]
  })
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep latest 25 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 25
        }
        action = { type = "expire" }
      }
    ]
  })
}
