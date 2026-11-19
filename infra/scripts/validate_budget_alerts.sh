#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 4 ]]; then
  cat <<'USAGE'
Usage:
  validate_budget_alerts.sh <budget-name> [expected-actual-threshold] [expected-forecast-threshold] [output-json]

Examples:
  validate_budget_alerts.sh last-mile-delivery-staging-monthly-total
  validate_budget_alerts.sh last-mile-delivery-staging-monthly-total 80 100 /tmp/budget-validation.json
USAGE
  exit 1
fi

BUDGET_NAME="$1"
EXPECTED_ACTUAL_THRESHOLD="${2:-80}"
EXPECTED_FORECAST_THRESHOLD="${3:-100}"
OUTPUT_JSON="${4:-}"
INPUT_JSON_FILE="${BUDGET_VALIDATION_INPUT_JSON:-}"
ACCOUNT_ID="${BUDGET_VALIDATION_ACCOUNT_ID:-}"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required." >&2
  exit 1
fi

if [[ -n "${INPUT_JSON_FILE}" ]]; then
  if [[ ! -f "${INPUT_JSON_FILE}" ]]; then
    echo "BUDGET_VALIDATION_INPUT_JSON file not found: ${INPUT_JSON_FILE}" >&2
    exit 1
  fi
  RAW_JSON="$(cat "${INPUT_JSON_FILE}")"
else
  if ! command -v aws >/dev/null 2>&1; then
    echo "aws CLI is required when BUDGET_VALIDATION_INPUT_JSON is not provided." >&2
    exit 1
  fi

  if [[ -z "${ACCOUNT_ID}" ]]; then
    ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  fi

  RAW_JSON="$(aws budgets describe-notifications-for-budget \
    --account-id "${ACCOUNT_ID}" \
    --budget-name "${BUDGET_NAME}" \
    --output json)"
fi

ACTUAL_THRESHOLD="$(echo "${RAW_JSON}" | jq -r '[.Notifications[]? | select(.Notification.NotificationType == "ACTUAL") | .Notification.Threshold] | first // empty')"
FORECAST_THRESHOLD="$(echo "${RAW_JSON}" | jq -r '[.Notifications[]? | select(.Notification.NotificationType == "FORECASTED") | .Notification.Threshold] | first // empty')"
SUBSCRIBER_COUNT="$(echo "${RAW_JSON}" | jq -r '[.Notifications[]?.Subscribers[]?] | length')"

if [[ -z "${ACTUAL_THRESHOLD}" ]]; then
  echo "Missing ACTUAL threshold notification for budget ${BUDGET_NAME}." >&2
  exit 1
fi

if [[ -z "${FORECAST_THRESHOLD}" ]]; then
  echo "Missing FORECASTED threshold notification for budget ${BUDGET_NAME}." >&2
  exit 1
fi

if [[ "${SUBSCRIBER_COUNT}" == "0" ]]; then
  echo "No budget notification subscribers configured for budget ${BUDGET_NAME}." >&2
  exit 1
fi

ACTUAL_THRESHOLD_INT="$(printf "%.0f" "${ACTUAL_THRESHOLD}")"
FORECAST_THRESHOLD_INT="$(printf "%.0f" "${FORECAST_THRESHOLD}")"
EXPECTED_ACTUAL_INT="$(printf "%.0f" "${EXPECTED_ACTUAL_THRESHOLD}")"
EXPECTED_FORECAST_INT="$(printf "%.0f" "${EXPECTED_FORECAST_THRESHOLD}")"

if [[ "${ACTUAL_THRESHOLD_INT}" != "${EXPECTED_ACTUAL_INT}" ]]; then
  echo "ACTUAL threshold mismatch: expected ${EXPECTED_ACTUAL_INT}, got ${ACTUAL_THRESHOLD_INT}" >&2
  exit 1
fi

if [[ "${FORECAST_THRESHOLD_INT}" != "${EXPECTED_FORECAST_INT}" ]]; then
  echo "FORECASTED threshold mismatch: expected ${EXPECTED_FORECAST_INT}, got ${FORECAST_THRESHOLD_INT}" >&2
  exit 1
fi

RESULT_JSON="$(jq -n \
  --arg budget_name "${BUDGET_NAME}" \
  --arg actual_threshold "${ACTUAL_THRESHOLD}" \
  --arg forecast_threshold "${FORECAST_THRESHOLD}" \
  --arg subscriber_count "${SUBSCRIBER_COUNT}" \
  --arg expected_actual_threshold "${EXPECTED_ACTUAL_THRESHOLD}" \
  --arg expected_forecast_threshold "${EXPECTED_FORECAST_THRESHOLD}" \
  --arg validated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{
    budget_name: $budget_name,
    actual_threshold: ($actual_threshold | tonumber),
    forecast_threshold: ($forecast_threshold | tonumber),
    subscriber_count: ($subscriber_count | tonumber),
    expected_actual_threshold: ($expected_actual_threshold | tonumber),
    expected_forecast_threshold: ($expected_forecast_threshold | tonumber),
    status: "pass",
    validated_at: $validated_at
  }'
)"

if [[ -n "${OUTPUT_JSON}" ]]; then
  mkdir -p "$(dirname "${OUTPUT_JSON}")"
  printf "%s\n" "${RESULT_JSON}" > "${OUTPUT_JSON}"
  echo "Budget validation report written to ${OUTPUT_JSON}"
else
  printf "%s\n" "${RESULT_JSON}"
fi
