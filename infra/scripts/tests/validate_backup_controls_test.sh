#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$repo_root"

output="$(infra/scripts/validate_backup_controls.sh)"
printf '%s\n' "$output"

grep -q "backup control validation passed" <<<"$output"
