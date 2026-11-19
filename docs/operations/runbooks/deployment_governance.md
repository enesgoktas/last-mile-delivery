# Deployment Governance Runbook

## Scope
This runbook defines who may approve deployments, what evidence is required before promotion, and how rollback authority works for staging and production.

## Approval Policy
- Staging deployments require:
  - release candidate commit identified
  - green `release-readiness` artifact bundle
  - one reviewer other than the author for application or infrastructure changes
- Production deployments require:
  - successful staging deployment on the same commit
  - green `release-readiness` artifact bundle for the promoted commit
  - two-person rule: one deploy operator and one approver who is not the change author
  - explicit rollback plan recorded before promotion

## Environment Controls
- Production deploy credentials must come from GitHub OIDC role assumption only.
- Protected branches must block direct pushes and require pull-request review before merge.
- Production deployment workflows should target protected GitHub environments with required reviewers enabled.
- Terraform apply and backend deployment jobs must retain artifacts for audit and rollback review.

## Required Promotion Evidence
- release candidate commit SHA
- release-readiness artifact bundle
- staging verification notes
- approver name/role
- deploy operator name/role
- rollback plan reference
- production outcome and rollback decision

Store promotion records in [evidence/deployment_approvals](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/deployment_approvals).

## Rollback Authority
- The deploy operator may halt promotion at any time.
- Any approver may require rollback if health checks, alarm posture, or data-migration signals are unclear.
- `SEV-1` and `SEV-2` incidents suspend new production deploys until incident command clears the freeze.

## Exit Criteria
- Approval evidence is recorded.
- Deployment artifacts are retained.
- Rollback decision is explicit.
- Section 11 ledger and release notes reflect the actual promoted commit.
