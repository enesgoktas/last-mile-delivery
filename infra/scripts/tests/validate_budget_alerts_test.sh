#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FIXTURE_PATH="${REPO_ROOT}/infra/scripts/fixtures/budget_notifications_sample.json"
TMP_DIR="$(mktemp -d)"
OUTPUT_PATH="${TMP_DIR}/budget-validation.json"

BUDGET_VALIDATION_INPUT_JSON="${FIXTURE_PATH}" \
  "${REPO_ROOT}/infra/scripts/validate_budget_alerts.sh" \
  "last-mile-delivery-staging-monthly-total" \
  80 \
  100 \
  "${OUTPUT_PATH}"

jq -e '.status == "pass"' "${OUTPUT_PATH}" >/dev/null
jq -e '.actual_threshold == 80' "${OUTPUT_PATH}" >/dev/null
jq -e '.forecast_threshold == 100' "${OUTPUT_PATH}" >/dev/null
jq -e '.subscriber_count == 2' "${OUTPUT_PATH}" >/dev/null

echo "validate_budget_alerts.sh fixture test passed. Output: ${OUTPUT_PATH}"
