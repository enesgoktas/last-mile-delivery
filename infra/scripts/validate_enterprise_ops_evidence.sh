#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.."
cd "$repo_root"

assert_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "missing required enterprise ops artifact: $path" >&2
    exit 1
  fi
}

assert_contains() {
  local needle="$1"
  local file="$2"
  if ! rg -q --fixed-strings "$needle" "$file"; then
    echo "missing expected enterprise ops text in $file: $needle" >&2
    exit 1
  fi
}

assert_evidence_example() {
  local dir="$1"
  if ! find "$dir" -maxdepth 1 -type f -name '*.md' ! -name 'README.md' | grep -q .; then
    echo "missing example enterprise ops evidence file in $dir" >&2
    exit 1
  fi
}

incident_doc="docs/operations/runbooks/incident_response.md"
incident_log="docs/operations/runbooks/incident_response_execution_log.md"
rotation_doc="docs/operations/runbooks/secret_rotation.md"
rotation_log="docs/operations/runbooks/secret_rotation_execution_log.md"
rotation_evidence="docs/operations/evidence/secret_rotation/README.md"
deployment_doc="docs/operations/runbooks/deployment_governance.md"
deployment_evidence="docs/operations/evidence/deployment_approvals/README.md"
evidence_index="docs/operations/enterprise_evidence_index.md"
security_doc="docs/operations/security.md"
security_governance_doc="docs/operations/security_governance.md"
access_review_evidence="docs/operations/evidence/access_reviews/README.md"
env_doc="docs/operations/environment_matrix.md"

assert_file "$incident_doc"
assert_file "$incident_log"
assert_file "$rotation_doc"
assert_file "$rotation_log"
assert_file "$rotation_evidence"
assert_file "$deployment_doc"
assert_file "$deployment_evidence"
assert_file "$evidence_index"
assert_file "$security_governance_doc"
assert_file "$access_review_evidence"
assert_file "docs/operations/evidence/incidents/README.md"
assert_file "backend/scripts/rotate_encrypted_secrets.py"
assert_evidence_example "docs/operations/evidence/incidents"
assert_evidence_example "docs/operations/evidence/secret_rotation"
assert_evidence_example "docs/operations/evidence/deployment_approvals"
assert_evidence_example "docs/operations/evidence/access_reviews"

assert_contains "Severity Levels" "$incident_doc"
assert_contains "First 30 Minutes" "$incident_doc"
assert_contains "Communication Rules" "$incident_doc"
assert_contains "incident_response_execution_log.md" "$incident_doc"
assert_contains "JWT_SECRET" "$rotation_doc"
assert_contains "TRACKING_JWT_SECRET" "$rotation_doc"
assert_contains "WEBHOOK_SECRET_ENCRYPTION_KEY" "$rotation_doc"
assert_contains "AUTH_JWT_ACTIVE_KID" "$rotation_doc"
assert_contains "rotate_encrypted_secrets.py" "$rotation_doc"
assert_contains "two-person rule" "$deployment_doc"
assert_contains "rollback plan" "$deployment_doc"
assert_contains "evidence/deployment_approvals" "$deployment_doc"
assert_contains "critical: same day" "$security_governance_doc"
assert_contains "quarterly" "$security_governance_doc"
assert_contains "Break-Glass Access" "$security_governance_doc"
assert_contains "Deployment controls: documented and CI-enforced." "$evidence_index"
assert_contains "deployment_governance.md" "$evidence_index"
assert_contains "incident_response.md" "$evidence_index"
assert_contains "secret_rotation.md" "$evidence_index"
assert_contains "security_governance.md" "$evidence_index"
assert_contains "Known Limits" "$evidence_index"
assert_contains "secret_rotation.md" "$security_doc"
assert_contains "security_governance.md" "$security_doc"
assert_contains "TRACKING_JWT_SECRET" "$env_doc"
assert_contains "OIDC_SECRET_ENCRYPTION_KEY" "$env_doc"
assert_contains "AUTH_JWT_PREVIOUS_KEYS" "$env_doc"
assert_contains "TRACKING_JWT_PREVIOUS_KEYS" "$env_doc"
assert_contains "MAINTENANCE_DATABASE_URL" "$env_doc"

echo "enterprise ops evidence validation passed"
