#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

output="$(bash infra/scripts/validate_enterprise_ops_evidence.sh)"
printf '%s\n' "$output"

grep -q "enterprise ops evidence validation passed" <<<"$output"
