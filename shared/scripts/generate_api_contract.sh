#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

openapi_typescript_version="${OPENAPI_TYPESCRIPT_VERSION:-7.13.0}"

python3 backend/scripts/export_openapi_schema.py --output shared/openapi.generated.json
npx --yes "openapi-typescript@${openapi_typescript_version}" \
  shared/openapi.generated.json \
  -o shared/api-schema.generated.ts
