# Local Webhook Secret Rotation Rehearsal

- Conducted at: `2026-03-27T05:09:23Z`
- Environment: `local-rehearsal`
- Operator: `codex`
- Target scope: `webhook_secret_encryption`
- Result: `BLOCKED_SCHEMA_MISMATCH`

## Commands and Observations

Database state:
```text
delivery_db|webhook_endpoints|0
20260314_0015
```

Dry run command:
```bash
cd backend
python3 scripts/rotate_encrypted_secrets.py \
  --database-url postgresql+asyncpg://delivery:delivery@localhost:5432/delivery_db \
  --old-seed tabletop-old-seed \
  --new-seed tabletop-new-seed \
  --targets webhooks
```

Observed result:
```text
RuntimeError: Webhook secret rotation failed because the database schema does not match the current application models. Run database migrations before using rotate_encrypted_secrets.py. Original error: <class 'asyncpg.exceptions.UndefinedColumnError'>: column webhook_endpoints.tenant_id does not exist
```

Verification:
```text
pytest -q backend/tests/unit/test_secret_rotation.py
7 passed
```

## Outcome
- No data was changed.
- The rehearsal exposed that the local Postgres instance is behind current application schema expectations.
- The rotation script now fails with an actionable migration message instead of a raw SQLAlchemy traceback.

## Follow-Up
- Re-run this rehearsal after the local database has been migrated to the current schema.
- Add a staging rotation rehearsal once the deployment target is fixed.
