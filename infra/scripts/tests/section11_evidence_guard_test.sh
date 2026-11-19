#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
GUARD_SCRIPT="${REPO_ROOT}/infra/scripts/check_section11_evidence.sh"
TMP_DIR="$(mktemp -d)"

base_checklist() {
  cat <<'MARKDOWN'
## Section 11: Infrastructure & Deployment
| id | deliverable | owner | artifact | test_evidence | status |
|---|---|---|---|---|---|
| 11.1 | AWS architecture IaC | platform | a | b | in_progress |
| 11.2 | Cost optimization controls | platform | a | b | in_progress |
| 11.3 | Monitoring/observability | platform | a | b | in_progress |
| 11.4 | CI/CD pipeline | platform | a | b | in_progress |
MARKDOWN
}

base_ledger() {
  cat <<'CSV'
run_id,commit_sha,environment,gate_id,artifact_refs,result,reviewer,timestamp_utc
CSV
}

# Case 1: all in_progress should pass without requiring ledger data.
CHECKLIST_1="${TMP_DIR}/checklist-1.md"
LEDGER_1="${TMP_DIR}/ledger-1.csv"
base_checklist > "${CHECKLIST_1}"
base_ledger > "${LEDGER_1}"
SECTION11_CHECKLIST_PATH="${CHECKLIST_1}" SECTION11_LEDGER_PATH="${LEDGER_1}" "${GUARD_SCRIPT}"

# Case 2: marked done without pass evidence must fail.
CHECKLIST_2="${TMP_DIR}/checklist-2.md"
LEDGER_2="${TMP_DIR}/ledger-2.csv"
base_checklist | sed 's/| 11.1 | AWS architecture IaC | platform | a | b | in_progress |/| 11.1 | AWS architecture IaC | platform | a | b | done |/' > "${CHECKLIST_2}"
base_ledger > "${LEDGER_2}"
if SECTION11_CHECKLIST_PATH="${CHECKLIST_2}" SECTION11_LEDGER_PATH="${LEDGER_2}" "${GUARD_SCRIPT}"; then
  echo "Expected guard failure when done gate lacks evidence." >&2
  exit 1
fi

# Case 3: marked done with staging+production pass evidence must succeed.
CHECKLIST_3="${TMP_DIR}/checklist-3.md"
LEDGER_3="${TMP_DIR}/ledger-3.csv"
base_checklist | sed 's/| 11.1 | AWS architecture IaC | platform | a | b | in_progress |/| 11.1 | AWS architecture IaC | platform | a | b | done |/' > "${CHECKLIST_3}"
cat > "${LEDGER_3}" <<'CSV'
run_id,commit_sha,environment,gate_id,artifact_refs,result,reviewer,timestamp_utc
r1,abc1234,staging,11.1,infra-plan-staging;infra-ci,pass,platform,2026-02-27T10:00:00Z
r2,abc1234,production,11.1,infra-plan-production;backend-cd,pass,platform,2026-02-27T12:00:00Z
CSV
SECTION11_CHECKLIST_PATH="${CHECKLIST_3}" SECTION11_LEDGER_PATH="${LEDGER_3}" "${GUARD_SCRIPT}"

echo "check_section11_evidence.sh fixture tests passed."
