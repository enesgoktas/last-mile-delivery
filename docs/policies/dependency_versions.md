# Dependency and Runtime Version Policy

## Backend
- Python: 3.11.x
- FastAPI: pinned minor updates only
- SQLAlchemy/Alembic: pinned
- Postgres: 16
- Redis: 7
- Celery: 5.x

## Frontend
- Node.js: LTS (>=20)
- React + TypeScript with pinned lockfile

## Mobile
- React Native aligned with supported LTS Node and Android/iOS SDK targets.

## Update policy
- Security updates: immediate patch window.
- Non-security: biweekly dependency review with changelog and regression checks.
