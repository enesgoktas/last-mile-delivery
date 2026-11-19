#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.."
cd "$repo_root"

prod_tfvars="infra/terraform/aws/environments/production.tfvars.example"
dr_doc="docs/operations/runbooks/disaster_recovery.md"

extract_number() {
  local key="$1"
  rg -oN "^${key}\\s*=\\s*[0-9]+" "$prod_tfvars" | awk -F'= ' '{print $2}'
}

extract_bool() {
  local key="$1"
  rg -oN "^${key}\\s*=\\s*(true|false)" "$prod_tfvars" | awk -F'= ' '{print $2}'
}

assert_contains() {
  local needle="$1"
  local file="$2"
  if ! rg -q --fixed-strings "$needle" "$file"; then
    echo "missing expected recovery posture text in $file: $needle" >&2
    exit 1
  fi
}

api_desired_count="$(extract_number "api_desired_count")"
worker_desired_count="$(extract_number "worker_desired_count")"
db_backup_retention_days="$(extract_number "db_backup_retention_days")"
redis_num_cache_clusters="$(extract_number "redis_num_cache_clusters")"
enable_nat_gateway="$(extract_bool "enable_nat_gateway")"
db_skip_final_snapshot="$(extract_bool "db_skip_final_snapshot")"

if [[ -z "$api_desired_count" || -z "$worker_desired_count" || -z "$db_backup_retention_days" ]]; then
  echo "failed to parse required production tfvars" >&2
  exit 1
fi

if (( api_desired_count < 2 )); then
  echo "production api_desired_count must be at least 2 for current DR posture" >&2
  exit 1
fi

if (( worker_desired_count < 2 )); then
  echo "production worker_desired_count must be at least 2 for current DR posture" >&2
  exit 1
fi

if (( db_backup_retention_days < 14 )); then
  echo "production db_backup_retention_days must be at least 14" >&2
  exit 1
fi

if [[ "$enable_nat_gateway" != "true" ]]; then
  echo "production enable_nat_gateway must remain true for private-subnet posture" >&2
  exit 1
fi

if [[ "$db_skip_final_snapshot" != "false" ]]; then
  echo "production db_skip_final_snapshot must remain false" >&2
  exit 1
fi

assert_contains 'multi_az               = var.environment == "production"' \
  'infra/terraform/aws/modules/data/main.tf'
assert_contains 'Current posture: single-region' "$dr_doc"
assert_contains 'Regional disaster failover is not currently supported.' "$dr_doc"
assert_contains 'Redis is single-node in the current production defaults.' "$dr_doc"
assert_contains 'PostgreSQL logical restore target: RPO <= 1h, RTO <= 4h.' "$dr_doc"
assert_contains 'API / worker service recovery target: RTO <= 30m.' "$dr_doc"

if (( redis_num_cache_clusters < 2 )); then
  echo "warning: redis_num_cache_clusters=${redis_num_cache_clusters}; Redis failover remains manual"
fi

echo "recovery posture validation passed"
