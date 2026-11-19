#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  cat <<'USAGE'
Usage:
  validate_required_alarms.sh <alarm-prefix> [output-json]

Example:
  validate_required_alarms.sh last-mile-delivery-staging /tmp/alarm-validation.json
USAGE
  exit 1
fi

ALARM_PREFIX="$1"
OUTPUT_JSON="${2:-}"
INPUT_JSON_FILE="${ALARM_VALIDATION_INPUT_JSON:-}"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required." >&2
  exit 1
fi

if [[ -n "${INPUT_JSON_FILE}" ]]; then
  if [[ ! -f "${INPUT_JSON_FILE}" ]]; then
    echo "ALARM_VALIDATION_INPUT_JSON file not found: ${INPUT_JSON_FILE}" >&2
    exit 1
  fi
  RAW_JSON="$(cat "${INPUT_JSON_FILE}")"
else
  if ! command -v aws >/dev/null 2>&1; then
    echo "aws CLI is required when ALARM_VALIDATION_INPUT_JSON is not provided." >&2
    exit 1
  fi
  RAW_JSON="$(aws cloudwatch describe-alarms --alarm-name-prefix "${ALARM_PREFIX}" --output json)"
fi

REQUIRED_SUFFIXES=(
  "alb-unhealthy-targets"
  "ecs-worker-cpu-high"
  "ecs-worker-memory-high"
  "rds-connections-high"
  "redis-evictions-high"
  "webhook-dead-letter-high"
)

missing=()
for suffix in "${REQUIRED_SUFFIXES[@]}"; do
  alarm_name="${ALARM_PREFIX}-${suffix}"
  exists="$(echo "${RAW_JSON}" | jq -r --arg name "${alarm_name}" '[.MetricAlarms[]? | select(.AlarmName == $name)] | length')"
  if [[ "${exists}" == "0" ]]; then
    missing+=("${alarm_name}")
  fi
done

if (( ${#missing[@]} > 0 )); then
  printf 'Missing required alarms:\n' >&2
  printf '  - %s\n' "${missing[@]}" >&2
  exit 1
fi

RESULT_JSON="$(echo "${RAW_JSON}" | jq -c --arg prefix "${ALARM_PREFIX}" '
  {
    alarm_prefix: $prefix,
    status: "pass",
    validated_at: (now | todateiso8601),
    alarms: ([.MetricAlarms[]?
      | select(.AlarmName | startswith($prefix + "-"))
      | {name: .AlarmName, state: .StateValue, actions_enabled: .ActionsEnabled}
    ] | sort_by(.name))
  }
')"

if [[ -n "${OUTPUT_JSON}" ]]; then
  mkdir -p "$(dirname "${OUTPUT_JSON}")"
  printf "%s\n" "${RESULT_JSON}" > "${OUTPUT_JSON}"
  echo "Alarm validation report written to ${OUTPUT_JSON}"
else
  printf "%s\n" "${RESULT_JSON}"
fi
