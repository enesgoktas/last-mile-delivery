#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

shared/scripts/generate_api_contract.sh
git diff --exit-code -- shared/openapi.generated.json shared/api-schema.generated.ts
