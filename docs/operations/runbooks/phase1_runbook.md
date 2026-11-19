# Phase 1 Runbook

## Local setup
1. Use **Python 3.11+** for local backend commands (`.python-version` at the repo root matches CI).
2. Start services: `cd backend && docker compose up -d`
3. Apply migrations: `cd backend && alembic upgrade head`
4. Seed admin user: `cd backend && python -m app.seed`
5. Seed full demo dataset for UI/manual QA: `cd backend && python -m app.seed_demo`
6. Run tests: `cd backend && pytest -q`
7. Start API: `cd backend && uvicorn app.main:app --reload`
8. Verify schema constraints/indexes: `cd backend && python scripts/schema_inspection_checks.py`

## Smoke checks
- `/health` returns `{"status": "ok"}`
- Auth login and token refresh succeed
- Manual order assignment works with constraints
- Courier location updates persist and cache to Redis

## Troubleshooting
- If `python -m uvicorn app.main:app --reload` gets killed with an `asyncpg`/`dlopen` policy error on macOS, reinstall `asyncpg` in the active interpreter:
  - `cd backend && python -m pip install --force-reinstall --no-cache-dir asyncpg==0.30.0`
- If port `8000` is already occupied:
  - `pkill -f 'uvicorn app.main:app'`
