#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CHECKLIST_PATH="${SECTION11_CHECKLIST_PATH:-${REPO_ROOT}/README.md}"
LEDGER_PATH="${SECTION11_LEDGER_PATH:-${REPO_ROOT}/docs/operations/evidence/section11/ledger.csv}"

GATES=("11.1" "11.2" "11.3" "11.4")
ENVS=("staging" "production")

if [[ ! -f "${CHECKLIST_PATH}" ]]; then
  echo "Checklist file not found: ${CHECKLIST_PATH}" >&2
  exit 1
fi

extract_status() {
  local gate="$1"
  local statuses
  local status_count

  statuses="$(
    awk -F'|' -v gate="${gate}" '
      $0 ~ "\\|[[:space:]]*" gate "[[:space:]]*\\|" {
        status = $7
        gsub(/\r/, "", status)
        gsub(/^[ \t]+|[ \t]+$/, "", status)
        print status
      }
    ' "${CHECKLIST_PATH}"
  )"

  if [[ -z "${statuses}" ]]; then
    echo "Missing checklist row for gate ${gate} in ${CHECKLIST_PATH}." >&2
    exit 1
  fi

  status_count="$(printf "%s\n" "${statuses}" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')"
  if [[ "${status_count}" != "1" ]]; then
    echo "Duplicate checklist rows for gate ${gate} in ${CHECKLIST_PATH}." >&2
    exit 1
  fi

  printf "%s\n" "${statuses}" | head -n 1
}

need_ledger=0
for gate in "${GATES[@]}"; do
  status="$(extract_status "${gate}")"
  if [[ "${status}" == "done" ]]; then
    need_ledger=1
  fi
done

if [[ "${need_ledger}" -eq 0 ]]; then
  echo "Section 11 rows are not marked done yet; evidence guard passed."
  exit 0
fi

if [[ ! -f "${LEDGER_PATH}" ]]; then
  echo "Evidence ledger not found: ${LEDGER_PATH}" >&2
  exit 1
fi

for gate in "${GATES[@]}"; do
  status="$(extract_status "${gate}")"
  if [[ "${status}" != "done" ]]; then
    continue
  fi

  for env in "${ENVS[@]}"; do
    if ! awk -F',' -v target_env="${env}" -v target_gate="${gate}" '
      NR > 1 {
        gsub(/\r/, "", $3)
        gsub(/\r/, "", $4)
        gsub(/\r/, "", $6)
        gsub(/^[ \t]+|[ \t]+$/, "", $3)
        gsub(/^[ \t]+|[ \t]+$/, "", $4)
        gsub(/^[ \t]+|[ \t]+$/, "", $6)
        if ($3 == target_env && $4 == target_gate && $6 == "pass") {
          found = 1
        }
      }
      END { exit(found ? 0 : 1) }
    ' "${LEDGER_PATH}"; then
      echo "Missing pass evidence for gate ${gate} in ${env}." >&2
      exit 1
    fi
  done
done

echo "Section 11 evidence guard passed."
