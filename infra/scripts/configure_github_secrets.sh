#!/usr/bin/env bash
# configure_github_secrets.sh — set all GitHub Actions secrets required by the CI/CD pipeline.
#
# Run this after:
#   1. provision_staging_secrets.sh   (gives TF_JWT_SECRET etc.)
#   2. run_bootstrap.sh               (gives TF_STATE_BUCKET / TF_STATE_LOCK_TABLE)
#   3. terraform apply                (gives ECS_CLUSTER, ECR repos, CodeDeploy values)
#
# All values are read from environment variables.  Nothing is hard-coded.
# The script validates that required vars are set before calling gh, so you get
# a clear list of what is still missing rather than a partial configuration.
#
# Usage:
#   # 1. Export every variable listed in the "Required env vars" section below.
#   # 2. Run:
#   ./configure_github_secrets.sh [--repo owner/repo]
#
# Optional env vars:
#   GITHUB_REPO   owner/repo slug (default: auto-detected from git remote)
#   GH_TOKEN      GitHub token if gh is not already authenticated
#
# Required env vars — Shared AWS OIDC:
#   AWS_REGION
#   AWS_ROLE_TO_ASSUME          OIDC role ARN for GitHub Actions to assume
#
# Required env vars — Terraform plan:
#   TF_STATE_BUCKET             Output of run_bootstrap.sh
#   TF_STATE_LOCK_TABLE         Output of run_bootstrap.sh
#   TF_DB_PASSWORD              Output of provision_staging_secrets.sh
#   TF_JWT_SECRET               Output of provision_staging_secrets.sh
#   TF_WEBHOOK_SECRET_ENCRYPTION_KEY  Output of provision_staging_secrets.sh
#   TF_ALB_CERTIFICATE_ARN      ACM certificate ARN (obtain from AWS Console / CLI)
#
# Required env vars — Backend CD (available after terraform apply):
#   ECS_CLUSTER                 Output of terraform apply
#   ECS_API_SERVICE             Output of terraform apply
#   ECS_WORKER_SERVICE          Output of terraform apply
#   ECR_BACKEND_REPOSITORY      Output of terraform apply (full URI)
#   ECR_WORKER_REPOSITORY       Output of terraform apply (full URI)
#   CODEDEPLOY_API_APPLICATION  Output of terraform apply
#   CODEDEPLOY_API_DEPLOYMENT_GROUP  Output of terraform apply
#   ECS_SUBNETS_CSV             Comma-separated private subnet IDs (terraform output)
#   ECS_SECURITY_GROUPS_CSV     Comma-separated ECS task security group IDs (terraform output)
#
# Optional env vars — Backend CD:
#   ECS_MIGRATION_TASK_DEFINITION_ARN  (leave unset to skip migration task)
#   API_HEALTHCHECK_URL                (leave unset to skip health gate override)

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
REPO_FLAG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO_FLAG="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: GitHub CLI (gh) is required. https://cli.github.com" >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: gh is not authenticated. Run 'gh auth login' first." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Resolve repo slug
# ---------------------------------------------------------------------------
if [[ -n "${REPO_FLAG}" ]]; then
  REPO="${REPO_FLAG}"
elif [[ -n "${GITHUB_REPO:-}" ]]; then
  REPO="${GITHUB_REPO}"
else
  REMOTE_URL="$(git remote get-url origin 2>/dev/null || echo "")"
  if [[ -z "${REMOTE_URL}" ]]; then
    echo "ERROR: cannot auto-detect repo. Set GITHUB_REPO=owner/repo or pass --repo owner/repo." >&2
    exit 1
  fi
  # Parse SSH (git@github.com:owner/repo.git) or HTTPS (https://github.com/owner/repo.git)
  REPO="$(echo "${REMOTE_URL}" | sed -E 's#.*[:/]([^/]+/[^/]+?)(\.git)?$#\1#')"
fi

echo "Target repository: ${REPO}"
echo ""

# ---------------------------------------------------------------------------
# Helper: set one secret, printing a [SET] or [SKIP] line
# ---------------------------------------------------------------------------
set_secret() {
  local secret_name="$1"
  local secret_value="$2"
  local required="${3:-true}"

  if [[ -z "${secret_value}" ]]; then
    if [[ "${required}" == "true" ]]; then
      # Collected above in MISSING array — don't set here
      return
    else
      echo "  [SKIP]  ${secret_name}  (not set — optional)"
      return
    fi
  fi

  printf '%s' "${secret_value}" | gh secret set "${secret_name}" --repo "${REPO}" --body -
  echo "  [SET]   ${secret_name}"
}

# ---------------------------------------------------------------------------
# Validate required env vars before touching GitHub
# ---------------------------------------------------------------------------
REQUIRED_VARS=(
  AWS_REGION
  AWS_ROLE_TO_ASSUME
  TF_STATE_BUCKET
  TF_STATE_LOCK_TABLE
  TF_DB_PASSWORD
  TF_JWT_SECRET
  TF_WEBHOOK_SECRET_ENCRYPTION_KEY
  TF_ALB_CERTIFICATE_ARN
  ECS_CLUSTER
  ECS_API_SERVICE
  ECS_WORKER_SERVICE
  ECR_BACKEND_REPOSITORY
  ECR_WORKER_REPOSITORY
  CODEDEPLOY_API_APPLICATION
  CODEDEPLOY_API_DEPLOYMENT_GROUP
  ECS_SUBNETS_CSV
  ECS_SECURITY_GROUPS_CSV
)

MISSING=()
for var in "${REQUIRED_VARS[@]}"; do
  [[ -z "${!var:-}" ]] && MISSING+=("${var}")
done

if (( ${#MISSING[@]} > 0 )); then
  echo "ERROR: the following required environment variables are not set:" >&2
  printf '  %s\n' "${MISSING[@]}" >&2
  echo "" >&2
  echo "  See the header comments in this script for where each value comes from." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Set secrets
# ---------------------------------------------------------------------------
echo "Configuring GitHub Actions secrets for: ${REPO}"
echo ""

echo "--- Shared AWS OIDC ---"
set_secret "AWS_REGION"          "${AWS_REGION}"
set_secret "AWS_ROLE_TO_ASSUME"  "${AWS_ROLE_TO_ASSUME}"

echo ""
echo "--- Terraform plan ---"
set_secret "TF_STATE_BUCKET"                  "${TF_STATE_BUCKET}"
set_secret "TF_STATE_LOCK_TABLE"              "${TF_STATE_LOCK_TABLE}"
set_secret "TF_DB_PASSWORD"                   "${TF_DB_PASSWORD}"
set_secret "TF_JWT_SECRET"                    "${TF_JWT_SECRET}"
set_secret "TF_WEBHOOK_SECRET_ENCRYPTION_KEY" "${TF_WEBHOOK_SECRET_ENCRYPTION_KEY}"
set_secret "TF_ALB_CERTIFICATE_ARN"           "${TF_ALB_CERTIFICATE_ARN}"

echo ""
echo "--- Backend CD ---"
set_secret "ECS_CLUSTER"                     "${ECS_CLUSTER}"
set_secret "ECS_API_SERVICE"                 "${ECS_API_SERVICE}"
set_secret "ECS_WORKER_SERVICE"              "${ECS_WORKER_SERVICE}"
set_secret "ECR_BACKEND_REPOSITORY"          "${ECR_BACKEND_REPOSITORY}"
set_secret "ECR_WORKER_REPOSITORY"           "${ECR_WORKER_REPOSITORY}"
set_secret "CODEDEPLOY_API_APPLICATION"      "${CODEDEPLOY_API_APPLICATION}"
set_secret "CODEDEPLOY_API_DEPLOYMENT_GROUP" "${CODEDEPLOY_API_DEPLOYMENT_GROUP}"
set_secret "ECS_SUBNETS_CSV"                 "${ECS_SUBNETS_CSV}"
set_secret "ECS_SECURITY_GROUPS_CSV"         "${ECS_SECURITY_GROUPS_CSV}"

echo ""
echo "--- Backend CD (optional) ---"
set_secret "ECS_MIGRATION_TASK_DEFINITION_ARN" "${ECS_MIGRATION_TASK_DEFINITION_ARN:-}" "false"
set_secret "API_HEALTHCHECK_URL"               "${API_HEALTHCHECK_URL:-}"               "false"

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
echo ""
echo "================================================================="
echo " Verifying secrets are registered in GitHub:"
echo "================================================================="
gh secret list --repo "${REPO}" | sort
echo ""
echo "All required secrets set. The following workflows are now unblocked:"
echo "  - infra-plan.yml     (Terraform plan for staging/production)"
echo "  - backend-cd.yml     (ECS blue/green deployment)"
echo "  - infra-ci.yml       (Terraform fmt/validate/tflint/tfsec)"
echo "================================================================="
