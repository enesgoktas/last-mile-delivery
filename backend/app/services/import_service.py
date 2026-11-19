from __future__ import annotations

import asyncio
import csv
import io
import logging
import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.algorithms.photon_client import geocode_address
from app.models.enums import ImportJobStatus, OrderPriority, OrderStatus, OrderType
from app.models.import_job import ImportJob
from app.schemas.order import OrderCreate
from app.services import order_service
from app.utils.compat import utcnow_naive
from app.utils.tenant_context import tenant_filter

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {
    "external_ref",
    "pickup_address",
    "delivery_address",
    "weight_kg",
    "is_fragile",
    "earliest_delivery",
    "latest_delivery",
    "customer_email",
    "customer_phone",
    "notes",
}


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _parse_iso_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.strip())


async def _geocode_addresses(
    addresses: set[str],
    *,
    max_concurrency: int = 12,
) -> dict[str, tuple[float, float] | None]:
    semaphore = asyncio.Semaphore(max_concurrency)
    cache: dict[str, tuple[float, float] | None] = {}

    async def _resolve(address: str) -> tuple[str, tuple[float, float] | None]:
        async with semaphore:
            try:
                return address, await geocode_address(address)
            except Exception:
                logger.exception("Geocoding failed for address: %s", address)
                return address, None

    for address, coords in await asyncio.gather(*(_resolve(a) for a in sorted(addresses))):
        cache[address] = coords
    return cache


async def preview_csv_import(
    db: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    tenant_id,
    csv_content: str,
) -> ImportJob:
    reader = csv.DictReader(io.StringIO(csv_content))
    header = set(reader.fieldnames or [])

    missing = _REQUIRED_COLUMNS - header
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing required columns: {sorted(missing)}",
        )

    rows = list(reader)
    unique_addresses = {
        str(address).strip()
        for row in rows
        for address in (row.get("pickup_address"), row.get("delivery_address"))
        if str(address).strip()
    }
    geocode_cache = await _geocode_addresses(unique_addresses)

    valid_payload: list[dict] = []
    invalid_details: list[dict] = []

    for row_index, row in enumerate(rows, start=2):
        errors: list[str] = []

        try:
            weight_kg = float(row["weight_kg"])
            earliest_delivery = _parse_iso_dt(row["earliest_delivery"])
            latest_delivery = _parse_iso_dt(row["latest_delivery"])
        except (TypeError, ValueError):
            errors.append("Invalid numeric/date field")
            weight_kg = 0.0
            fallback_delivery_time = utcnow_naive()
            earliest_delivery = fallback_delivery_time
            latest_delivery = fallback_delivery_time

        pickup_address = row["pickup_address"].strip()
        delivery_address = row["delivery_address"].strip()
        pickup_coords = geocode_cache.get(pickup_address)
        if pickup_coords is None:
            errors.append("Pickup address could not be geocoded")

        delivery_coords = geocode_cache.get(delivery_address)
        if delivery_coords is None:
            errors.append("Delivery address could not be geocoded")

        if latest_delivery <= earliest_delivery:
            errors.append("latest_delivery must be after earliest_delivery")

        if weight_kg <= 0:
            errors.append("weight_kg must be > 0")

        if errors:
            invalid_details.append(
                {
                    "row_number": row_index,
                    "errors": errors,
                    "raw": row,
                }
            )
            continue

        assert pickup_coords is not None
        assert delivery_coords is not None

        valid_payload.append(
            {
                "type": OrderType.BULK.value,
                "priority": OrderPriority.NORMAL.value,
                "pickup_address": pickup_address,
                "pickup_lat": pickup_coords[0],
                "pickup_lon": pickup_coords[1],
                "delivery_address": delivery_address,
                "delivery_lat": delivery_coords[0],
                "delivery_lon": delivery_coords[1],
                "weight_kg": weight_kg,
                "is_fragile": _to_bool(row["is_fragile"]),
                "is_temp_sensitive": False,
                "earliest_pickup": None,
                "latest_pickup": None,
                "earliest_delivery": earliest_delivery.isoformat(),
                "latest_delivery": latest_delivery.isoformat(),
                "customer_email": row["customer_email"],
                "customer_phone": row["customer_phone"] or None,
                "external_ref": row["external_ref"] or None,
                "notes": row["notes"] or None,
            }
        )

    job = ImportJob(
        tenant_id=tenant_id,
        created_by_user_id=actor_user_id,
        status=ImportJobStatus.PREVIEW_READY,
        total_rows=(len(valid_payload) + len(invalid_details)),
        valid_rows=len(valid_payload),
        invalid_rows=len(invalid_details),
        payload=valid_payload,
        invalid_details=invalid_details,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return job


async def publish_import_job(
    db: AsyncSession,
    *,
    import_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    tenant_id,
) -> tuple[ImportJob, list[uuid.UUID]]:
    result = await db.execute(
        select(ImportJob).where(
            ImportJob.id == import_id,
            tenant_filter(ImportJob, tenant_id),
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found")

    if job.created_by_user_id != actor_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Import job belongs to another user")

    if job.status != ImportJobStatus.PREVIEW_READY:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Import job is not publishable")

    created_ids: list[uuid.UUID] = []
    for row in job.payload:
        created = await order_service.create_order(
            db,
            OrderCreate(**row),
            tenant_id=job.tenant_id,
            initial_status=OrderStatus.DRAFT,
            creation_reason="Imported as draft",
            actor_user_id=actor_user_id,
        )
        if created.status == OrderStatus.DRAFT:
            await order_service.apply_order_status_transition(
                db,
                created,
                OrderStatus.PENDING,
                actor_user_id=actor_user_id,
                reason="Import published",
            )
        created_ids.append(created.id)

    job.status = ImportJobStatus.PUBLISHED
    job.published_at = utcnow_naive()
    await db.flush()
    await db.refresh(job)
    return job, created_ids


async def get_import_job_for_user(
    db: AsyncSession,
    *,
    import_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    tenant_id,
) -> ImportJob:
    result = await db.execute(
        select(ImportJob).where(
            ImportJob.id == import_id,
            tenant_filter(ImportJob, tenant_id),
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found")
    if job.created_by_user_id != actor_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Import job belongs to another user")
    return job


def render_invalid_rows_csv(job: ImportJob) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "row_number",
            "errors",
            "external_ref",
            "pickup_address",
            "delivery_address",
            "weight_kg",
            "earliest_delivery",
            "latest_delivery",
            "customer_email",
            "customer_phone",
            "notes",
        ]
    )
    for item in job.invalid_details or []:
        raw = item.get("raw", {}) if isinstance(item, dict) else {}
        errors = item.get("errors", []) if isinstance(item, dict) else []
        writer.writerow(
            [
                item.get("row_number"),
                "; ".join(errors),
                raw.get("external_ref", ""),
                raw.get("pickup_address", ""),
                raw.get("delivery_address", ""),
                raw.get("weight_kg", ""),
                raw.get("earliest_delivery", ""),
                raw.get("latest_delivery", ""),
                raw.get("customer_email", ""),
                raw.get("customer_phone", ""),
                raw.get("notes", ""),
            ]
        )
    return output.getvalue()
