# Security Governance

## Ownership
- Engineering owns day-to-day application security controls and remediation work.
- A named release approver owns production deployment risk acceptance for each release.
- Security-sensitive changes require reviewer coverage from someone other than the author.

## Vulnerability Management
- Dependency and static-analysis findings are triaged on every release candidate.
- Severity targets:
  - critical: same day or emergency mitigation
  - high: within 7 calendar days
  - medium: within 30 calendar days
  - low: within 90 calendar days or next planned maintenance window
- If a fix cannot land in time, the repo should contain a documented exception, owner, and expiration date.

## Access Reviews
- Production secrets, deploy roles, and admin access are reviewed at least quarterly.
- Leavers or vendor offboarding events trigger immediate access review outside the normal cadence.
- Review evidence is stored in [evidence/access_reviews](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/access_reviews).

## Break-Glass Access
- Break-glass access is only for production incidents or blocked recovery actions.
- Use temporary credentials or an emergency role with the smallest possible scope.
- Every break-glass event must record:
  - reason
  - who approved it
  - who used it
  - start and end time
  - follow-up credential rotation or revocation steps

## Review Cadence
- Security review checklist refresh: monthly or after major auth/integration changes.
- Incident response and secret rotation drills: at least quarterly.
- Backup / restore rehearsal: at least quarterly for staging and after major infrastructure changes.
