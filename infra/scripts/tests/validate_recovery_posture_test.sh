#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

output="$(infra/scripts/validate_recovery_posture.sh)"
printf '%s\n' "$output"

grep -q "recovery posture validation passed" <<<"$output"
grep -q "warning: redis_num_cache_clusters=1; Redis failover remains manual" <<<"$output"
