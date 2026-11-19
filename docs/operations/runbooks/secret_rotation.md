# Secret Rotation Runbook

## Scope
This runbook covers:
- staff auth JWT signing keys
- public tracking JWT signing keys
- encrypted webhook endpoint secrets
- encrypted OIDC provider client secrets

Record every execution in [secret_rotation_execution_log.md](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/runbooks/secret_rotation_execution_log.md) and store artifacts in [docs/operations/evidence/secret_rotation](/Users/dorukyalcin/Desktop/LastMileDelivery/docs/operations/evidence/secret_rotation).

## Preconditions
- Confirm the reason for rotation: scheduled hygiene, suspected compromise, vendor offboarding, or incident response.
- Freeze risky deploys during the rotation window.
- Ensure the new values are staged in the secret manager before changing active settings.
- Keep a rollback window for auth/tracking rotations by preserving previous keys temporarily.
- Confirm the target database has current migrations applied before running `rotate_encrypted_secrets.py`.

## Auth JWT Rotation
1. Generate a new JWT secret.
2. Choose a new `AUTH_JWT_ACTIVE_KID`.
3. Move the old active secret into `AUTH_JWT_PREVIOUS_KEYS` under its old `kid`.
4. Deploy the new `JWT_SECRET`, `AUTH_JWT_ACTIVE_KID`, and `AUTH_JWT_PREVIOUS_KEYS`.
5. Verify new login and refresh tokens are issued with the new `kid`.
6. Wait for the refresh-token rollover window to pass.
7. Remove the previous key from `AUTH_JWT_PREVIOUS_KEYS`.

Required config keys:
- `JWT_SECRET`
- `AUTH_JWT_ACTIVE_KID`
- `AUTH_JWT_PREVIOUS_KEYS`

## Tracking JWT Rotation
1. Generate a new `TRACKING_JWT_SECRET`.
2. Choose a new `TRACKING_JWT_ACTIVE_KID`.
3. Move the old tracking secret into `TRACKING_JWT_PREVIOUS_KEYS`.
4. Deploy the new tracking secret and key IDs.
5. Verify newly generated tracking links carry the new `kid`.
6. Wait for the active tracking-token lifetime to pass before removing the old key.

Required config keys:
- `TRACKING_JWT_SECRET`
- `TRACKING_JWT_ACTIVE_KID`
- `TRACKING_JWT_PREVIOUS_KEYS`

## Webhook and OIDC Secret Re-encryption
Webhook endpoint secrets and OIDC provider client secrets are stored encrypted at the application layer. When rotating `WEBHOOK_SECRET_ENCRYPTION_KEY` or `OIDC_SECRET_ENCRYPTION_KEY`, re-encrypt the stored values first.

Dry run:
```bash
cd backend
python3 scripts/rotate_encrypted_secrets.py \
  --old-seed "$OLD_SEED" \
  --new-seed "$NEW_SEED"
```

Apply rotation:
```bash
cd backend
python3 scripts/rotate_encrypted_secrets.py \
  --old-seed "$OLD_SEED" \
  --new-seed "$NEW_SEED" \
  --apply
```

Target only one class if needed:
```bash
cd backend
python3 scripts/rotate_encrypted_secrets.py \
  --old-seed "$OLD_SEED" \
  --new-seed "$NEW_SEED" \
  --targets oidc \
  --apply
```

After the DB values are re-encrypted:
1. Update `WEBHOOK_SECRET_ENCRYPTION_KEY` and/or `OIDC_SECRET_ENCRYPTION_KEY` in the runtime secret store.
2. Deploy the new runtime configuration.
3. Verify one webhook delivery and one OIDC login against staging or a safe test tenant.

## Evidence Capture
- reason for rotation
- environment
- old key ID and new key ID where applicable
- dry-run output and apply output from `rotate_encrypted_secrets.py`
- verification steps performed
- reviewer and rollback decision

## Exit Criteria
- New tokens or encrypted secrets are being issued with the new active material.
- Previous keys remain only for the planned compatibility window.
- Verification checks pass.
- Execution log entry and evidence references are complete.
