#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.."
cd "$repo_root"

assert_contains() {
  local needle="$1"
  local file="$2"
  if ! rg -q --fixed-strings "$needle" "$file"; then
    echo "missing expected backup control in $file: $needle" >&2
    exit 1
  fi
}

assert_contains 'backup_retention_period = var.db_backup_retention_days' \
  'infra/terraform/aws/modules/data/main.tf'
assert_contains 'multi_az               = var.environment == "production"' \
  'infra/terraform/aws/modules/data/main.tf'
assert_contains 'skip_final_snapshot = var.db_skip_final_snapshot' \
  'infra/terraform/aws/modules/data/main.tf'
assert_contains 'status = "Enabled"' 'infra/terraform/aws/modules/storage/main.tf'
assert_contains 'resource "aws_s3_bucket_versioning" "pod_assets"' \
  'infra/terraform/aws/modules/storage/main.tf'
assert_contains 'resource "aws_s3_bucket_versioning" "dashboard"' \
  'infra/terraform/aws/modules/storage/main.tf'
assert_contains 'resource "aws_s3_bucket_versioning" "tracking"' \
  'infra/terraform/aws/modules/storage/main.tf'
assert_contains 'db_backup_retention_days = 14' \
  'infra/terraform/aws/environments/production.tfvars.example'
assert_contains 'db_skip_final_snapshot   = false' \
  'infra/terraform/aws/environments/production.tfvars.example'
assert_contains 'point_in_time_recovery {' 'infra/terraform/bootstrap/main.tf'
assert_contains 'enabled = true' 'infra/terraform/bootstrap/main.tf'

echo "backup control validation passed"
