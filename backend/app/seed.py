"""Seed script — creates baseline users for development.

Usage:
    python -m app.seed
"""
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.database import async_session
from app.models import User
from app.models.enums import UserRole
from app.seed_helpers import ensure_default_tenant
from app.utils.auth import hash_password


def run_migrations() -> None:
    """Apply migrations before creating seed records."""
    root = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(alembic_cfg, "head")


async def seed() -> None:
    users_to_seed = [
        ("admin@delivery.com", "admin123", UserRole.ADMIN),
        ("dispatcher@delivery.com", "changeme123", UserRole.DISPATCHER),
    ]

    async with async_session() as session:
        default_tenant = await ensure_default_tenant(session)
        created = 0
        for email, password, role in users_to_seed:
            result = await session.execute(
                select(User).where(
                    User.email == email,
                    User.tenant_id == default_tenant.id,
                )
            )
            if result.scalar_one_or_none():
                continue
            session.add(
                User(
                    tenant_id=default_tenant.id,
                    email=email,
                    hashed_password=hash_password(password),
                    role=role,
                )
            )
            created += 1
        await session.commit()
        if created == 0:
            print("Seed users already exist, nothing to create.")
        else:
            print(
                "Seeded users: admin@delivery.com/admin123, "
                "dispatcher@delivery.com/changeme123"
            )


if __name__ == "__main__":
    run_migrations()
    asyncio.run(seed())
