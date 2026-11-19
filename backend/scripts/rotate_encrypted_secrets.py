from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@dataclass(frozen=True, slots=True)
class RotationCounts:
    webhook_endpoints: int = 0
    oidc_providers: int = 0


def _format_schema_mismatch_error(*, target: str, exc: ProgrammingError) -> str:
    original_error = getattr(exc, "orig", exc)
    return (
        f"{target} secret rotation failed because the database schema does not match "
        "the current application models. Run database migrations before using "
        "rotate_encrypted_secrets.py. "
        f"Original error: {original_error}"
    )


async def _rotate_webhook_secrets(
    db: AsyncSession,
    *,
    old_seed: str,
    new_seed: str,
    apply_changes: bool,
) -> int:
    from app.models.webhook import WebhookEndpoint
    from app.utils.secret_rotation import rotate_encrypted_value

    result = await db.execute(select(WebhookEndpoint))
    endpoints = list(result.scalars().all())
    for endpoint in endpoints:
        rotated = rotate_encrypted_value(
            endpoint.secret,
            old_seed=old_seed,
            new_seed=new_seed,
        )
        if apply_changes:
            endpoint.secret = rotated
    return len(endpoints)


async def _rotate_oidc_client_secrets(
    db: AsyncSession,
    *,
    old_seed: str,
    new_seed: str,
    apply_changes: bool,
) -> int:
    from app.models.oidc_provider import OIDCProvider
    from app.utils.secret_rotation import rotate_encrypted_value

    result = await db.execute(select(OIDCProvider))
    providers = list(result.scalars().all())
    for provider in providers:
        rotated = rotate_encrypted_value(
            provider.client_secret_enc,
            old_seed=old_seed,
            new_seed=new_seed,
        )
        if apply_changes:
            provider.client_secret_enc = rotated
    return len(providers)


async def rotate_secrets(
    *,
    database_url: str,
    old_seed: str,
    new_seed: str,
    rotate_webhooks: bool,
    rotate_oidc: bool,
    apply_changes: bool,
) -> RotationCounts:
    engine = create_async_engine(database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            webhook_count = 0
            oidc_count = 0
            try:
                if rotate_webhooks:
                    webhook_count = await _rotate_webhook_secrets(
                        db,
                        old_seed=old_seed,
                        new_seed=new_seed,
                        apply_changes=apply_changes,
                    )
                if rotate_oidc:
                    oidc_count = await _rotate_oidc_client_secrets(
                        db,
                        old_seed=old_seed,
                        new_seed=new_seed,
                        apply_changes=apply_changes,
                    )
            except ProgrammingError as exc:
                target = "OIDC provider" if rotate_oidc and not rotate_webhooks else "Webhook"
                if rotate_oidc and rotate_webhooks and oidc_count == 0 and webhook_count > 0:
                    target = "OIDC provider"
                raise RuntimeError(
                    _format_schema_mismatch_error(target=target, exc=exc)
                ) from exc

            counts = RotationCounts(
                webhook_endpoints=webhook_count,
                oidc_providers=oidc_count,
            )
            if apply_changes:
                await db.commit()
            else:
                await db.rollback()
            return counts
    finally:
        await engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    from app.config import settings

    parser = argparse.ArgumentParser(
        description=(
            "Re-encrypt stored webhook and OIDC provider secrets with a new seed. "
            "Defaults to dry-run; pass --apply to persist changes."
        )
    )
    parser.add_argument(
        "--database-url",
        default=settings.DATABASE_URL,
        help="SQLAlchemy database URL. Defaults to backend settings.DATABASE_URL.",
    )
    parser.add_argument("--old-seed", required=True, help="Current encryption seed.")
    parser.add_argument("--new-seed", required=True, help="Replacement encryption seed.")
    parser.add_argument(
        "--targets",
        choices=("all", "webhooks", "oidc"),
        default="all",
        help="Which encrypted secret sets to rotate.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist rotated secrets. Omit for dry-run counting only.",
    )
    return parser


def _format_summary(*, counts: RotationCounts, apply_changes: bool, targets: str) -> str:
    mode = "APPLY" if apply_changes else "DRY RUN"
    return (
        f"{mode} completed for {targets}: "
        f"webhook_endpoints={counts.webhook_endpoints}, "
        f"oidc_providers={counts.oidc_providers}"
    )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    rotate_webhooks = args.targets in {"all", "webhooks"}
    rotate_oidc = args.targets in {"all", "oidc"}
    counts = asyncio.run(
        rotate_secrets(
            database_url=args.database_url,
            old_seed=args.old_seed,
            new_seed=args.new_seed,
            rotate_webhooks=rotate_webhooks,
            rotate_oidc=rotate_oidc,
            apply_changes=args.apply,
        )
    )
    print(
        _format_summary(
            counts=counts,
            apply_changes=args.apply,
            targets=args.targets,
        )
    )


if __name__ == "__main__":
    main()
