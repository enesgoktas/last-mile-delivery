#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
AWS_TERRAFORM_DIR="${1:-${REPO_ROOT}/infra/terraform/aws}"
BOOTSTRAP_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/bootstrap"
TF_VERSION="${TF_VERSION:-1.6.6}"
TFLINT_VERSION="${TFLINT_VERSION:-v0.51.1}"
TFSEC_VERSION="${TFSEC_VERSION:-v1.28.5}"

if [[ ! -d "${AWS_TERRAFORM_DIR}" ]]; then
  echo "Terraform directory not found: ${AWS_TERRAFORM_DIR}" >&2
  exit 1
fi

if [[ ! -d "${BOOTSTRAP_TERRAFORM_DIR}" ]]; then
  echo "Bootstrap Terraform directory not found: ${BOOTSTRAP_TERRAFORM_DIR}" >&2
  exit 1
fi

run_in_dir() {
  local dir="$1"
  shift
  (
    cd "${dir}"
    "$@"
  )
}

if command -v terraform >/dev/null 2>&1; then
  run_tf() {
    local dir="$1"
    shift
    run_in_dir "${dir}" terraform "$@"
  }
  echo "Using local terraform binary."
elif command -v docker >/dev/null 2>&1; then
  run_tf() {
    local dir="$1"
    shift
    docker run --rm \
      --user "$(id -u):$(id -g)" \
      -v "${dir}:/workspace" \
      -w /workspace \
      "hashicorp/terraform:${TF_VERSION}" \
      "$@"
  }
  echo "Using Docker terraform image hashicorp/terraform:${TF_VERSION}."
else
  echo "Neither terraform nor docker is available on PATH." >&2
  exit 1
fi

if command -v tflint >/dev/null 2>&1; then
  run_tflint() {
    local dir="$1"
    shift
    run_in_dir "${dir}" tflint "$@"
  }
  echo "Using local tflint binary."
elif command -v docker >/dev/null 2>&1; then
  run_tflint() {
    local dir="$1"
    shift
    docker run --rm \
      --user "$(id -u):$(id -g)" \
      -v "${dir}:/workspace" \
      -w /workspace \
      "ghcr.io/terraform-linters/tflint:${TFLINT_VERSION}" \
      "$@"
  }
  echo "Using Docker tflint image ghcr.io/terraform-linters/tflint:${TFLINT_VERSION}."
else
  echo "Neither tflint nor docker is available on PATH." >&2
  exit 1
fi

if command -v tfsec >/dev/null 2>&1; then
  run_tfsec() {
    local dir="$1"
    shift
    run_in_dir "${dir}" tfsec "$@"
  }
  echo "Using local tfsec binary."
elif command -v docker >/dev/null 2>&1; then
  run_tfsec() {
    local dir="$1"
    shift
    docker run --rm \
      --user "$(id -u):$(id -g)" \
      -v "${dir}:/workspace" \
      -w /workspace \
      "aquasec/tfsec:${TFSEC_VERSION}" \
      "$@"
  }
  echo "Using Docker tfsec image aquasec/tfsec:${TFSEC_VERSION}."
else
  echo "Neither tfsec nor docker is available on PATH." >&2
  exit 1
fi

validate_dir() {
  local dir="$1"

  echo "Validating Terraform directory: ${dir}"
  run_tf "${dir}" fmt -check -recursive
  run_tf "${dir}" init -backend=false
  run_tf "${dir}" validate

  run_tflint "${dir}" --init
  run_tflint "${dir}" --recursive

  run_tfsec "${dir}" --minimum-severity HIGH .
}

validate_dir "${BOOTSTRAP_TERRAFORM_DIR}"
validate_dir "${AWS_TERRAFORM_DIR}"

for module_dir in "${AWS_TERRAFORM_DIR}"/modules/*; do
  if [[ -d "${module_dir}" ]]; then
    validate_dir "${module_dir}"
  fi
done

echo "Terraform fmt/init/validate + tflint + tfsec completed successfully."
