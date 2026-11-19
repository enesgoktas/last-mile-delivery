#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FIXTURE_PATH="${REPO_ROOT}/infra/scripts/fixtures/cost_explorer_sample.json"
TMP_DIR="$(mktemp -d)"
OUTPUT_PATH="${TMP_DIR}/cost-report.md"

COST_REPORT_INPUT_JSON="${FIXTURE_PATH}" \
  "${REPO_ROOT}/infra/scripts/monthly_cost_report.sh" \
  "2026-01-01" \
  "2026-02-01" \
  "${OUTPUT_PATH}"

grep -Fq "# Monthly AWS Cost Report (2026-01-01 to 2026-02-01)" "${OUTPUT_PATH}"
grep -Fq -- "- \$987.65" "${OUTPUT_PATH}"
grep -Fq "| Amazon Elastic Compute Cloud - Compute | 500.12 |" "${OUTPUT_PATH}"
grep -Fq "| Amazon Relational Database Service | 240.55 |" "${OUTPUT_PATH}"
grep -Fq "| Amazon ElastiCache | 120.10 |" "${OUTPUT_PATH}"

echo "monthly_cost_report.sh fixture test passed. Output: ${OUTPUT_PATH}"
