#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FIXTURE_PATH="${REPO_ROOT}/infra/scripts/fixtures/cloudwatch_alarms_sample.json"
TMP_DIR="$(mktemp -d)"
OUTPUT_PATH="${TMP_DIR}/alarm-validation.json"

ALARM_VALIDATION_INPUT_JSON="${FIXTURE_PATH}" \
  "${REPO_ROOT}/infra/scripts/validate_required_alarms.sh" \
  "last-mile-delivery-staging" \
  "${OUTPUT_PATH}"

jq -e '.status == "pass"' "${OUTPUT_PATH}" >/dev/null
jq -e '.alarms | length == 6' "${OUTPUT_PATH}" >/dev/null
jq -e '.alarms[] | select(.name == "last-mile-delivery-staging-redis-evictions-high")' "${OUTPUT_PATH}" >/dev/null
jq -e '.alarms[] | select(.name == "last-mile-delivery-staging-webhook-dead-letter-high")' "${OUTPUT_PATH}" >/dev/null

echo "validate_required_alarms.sh fixture test passed. Output: ${OUTPUT_PATH}"
