"""Recurring order materialisation.

The service is split into two halves:

  1. **Cadence helpers** — pure functions that, given a template and an
     anchor date, compute due occurrence dates. No DB, no side effects;
     these are exhaustively tested in `tests/unit/test_recurring_order_service.py`.
  2. **Materialisation** — `materialise_due_occurrences` walks active
     templates, computes due dates inside the lookahead window, and creates
     one PENDING `Order` per occurrence, idempotently.

Idempotency is enforced both by a unique
`(template_id, occurrence_date)` row in `recurring_order_occurrences` *and*
by a SAVEPOINT around each insert — a duplicate-key collision rolls back
the savepoint without aborting the rest of the batch.
"""
from __future__ import annotations

import logging
import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import OrderStatus, OrderType
from app.models.order import Order
from app.models.order_history import OrderStatusHistory
from app.models.recurring_order import (
    RecurrenceCadence,
    RecurringOrderOccurrence,
    RecurringOrderTemplate,
)
from app.services import operations_policy_service
from app.utils.auth import create_tracking_token
from app.utils.compat import utcnow_naive

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cadence helpers (pure)
# ---------------------------------------------------------------------------

_WEEKDAY_BIT = {i: 1 << i for i in range(7)}


def _weekly_active_days(mask: int, fallback_weekday: int) -> set[int]:
    """Return the set of weekday indices the template fires on."""
    if mask <= 0:
        return {fallback_weekday}
    return {wd for wd in range(7) if mask & _WEEKDAY_BIT[wd]}


def _add_months(anchor: date, months: int) -> date:
    """Add a number of calendar months, capping the day to the new month length."""
    year = anchor.year + (anchor.month - 1 + months) // 12
    month = (anchor.month - 1 + months) % 12 + 1
    last = monthrange(year, month)[1]
    return date(year, month, min(anchor.day, last))


def occurrences_in_range(
    template: RecurringOrderTemplate,
    *,
    range_start: date,
    range_end: date,
) -> list[date]:
    """Enumerate occurrence dates for a template inside [range_start, range_end].

    Both bounds are inclusive. Honours `start_date`, `end_date`, and
    `max_occurrences - occurrences_generated` (cap on remaining slots).
    """
    if range_end < range_start:
        return []

    earliest = max(range_start, template.start_date)
    latest = range_end
    if template.end_date is not None:
        latest = min(latest, template.end_date)
    if latest < earliest:
        return []

    remaining_slots: int | None = None
    if template.max_occurrences is not None:
        remaining_slots = max(0, template.max_occurrences - template.occurrences_generated)
        if remaining_slots == 0:
            return []

    cadence = template.cadence
    out: list[date] = []

    if cadence == RecurrenceCadence.DAILY:
        cursor = earliest
        while cursor <= latest:
            out.append(cursor)
            cursor += timedelta(days=1)

    elif cadence == RecurrenceCadence.WEEKLY:
        active = _weekly_active_days(
            template.day_of_week_mask, template.start_date.weekday()
        )
        cursor = earliest
        while cursor <= latest:
            if cursor.weekday() in active and cursor >= template.start_date:
                out.append(cursor)
            cursor += timedelta(days=1)

    elif cadence == RecurrenceCadence.BIWEEKLY:
        # Anchored to start_date; every 14 days.
        delta_days = (earliest - template.start_date).days
        if delta_days < 0:
            cursor = template.start_date
        else:
            offset = (14 - (delta_days % 14)) % 14
            cursor = earliest + timedelta(days=offset)
        while cursor <= latest:
            if cursor >= template.start_date:
                out.append(cursor)
            cursor += timedelta(days=14)

    elif cadence == RecurrenceCadence.MONTHLY:
        target_dom = template.day_of_month or template.start_date.day
        # Walk months from earliest's month back to template start_date's month.
        cursor_year, cursor_month = earliest.year, earliest.month
        while True:
            last = monthrange(cursor_year, cursor_month)[1]
            day = min(target_dom, last)
            candidate = date(cursor_year, cursor_month, day)
            if candidate > latest:
                break
            if candidate >= earliest and candidate >= template.start_date:
                out.append(candidate)
            # advance one month
            if cursor_month == 12:
                cursor_year, cursor_month = cursor_year + 1, 1
            else:
                cursor_month += 1

    elif cadence == RecurrenceCadence.CUSTOM:
        step = max(1, template.interval_days)
        delta_days = (earliest - template.start_date).days
        if delta_days < 0:
            cursor = template.start_date
        else:
            offset = (step - (delta_days % step)) % step
            cursor = earliest + timedelta(days=offset)
        while cursor <= latest:
            if cursor >= template.start_date:
                out.append(cursor)
            cursor += timedelta(days=step)

    if remaining_slots is not None:
        out = out[:remaining_slots]

    return out


def next_occurrence_after(
    template: RecurringOrderTemplate, after: date
) -> date | None:
    """Return the first occurrence date strictly after `after`, or None.

    Used by the materialiser to schedule its next wakeup.
    """
    horizon = after + timedelta(days=max(1, 31 + template.interval_days))
    candidates = occurrences_in_range(
        template, range_start=after + timedelta(days=1), range_end=horizon
    )
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_template_payload(payload: dict) -> None:
    """Raise HTTPException(422) on obviously invalid template inputs.

    Pydantic catches type errors; this catches semantic errors that depend on
    multiple fields (e.g. delivery window ordering, cadence × cadence-args).
    """
    cadence = payload.get("cadence")
    interval_days = payload.get("interval_days") or 1
    if cadence == RecurrenceCadence.CUSTOM and interval_days < 1:
        raise HTTPException(422, detail="interval_days must be >= 1 for CUSTOM cadence")

    dws = payload.get("delivery_window_start")
    dwe = payload.get("delivery_window_end")
    if dws and dwe and dws >= dwe:
        raise HTTPException(
            422,
            detail="delivery_window_end must be later than delivery_window_start",
        )

    pws = payload.get("pickup_window_start")
    pwe = payload.get("pickup_window_end")
    if (pws is None) != (pwe is None):
        raise HTTPException(
            422,
            detail="pickup_window_start and pickup_window_end must both be set or both be omitted",
        )
    if pws and pwe and pws >= pwe:
        raise HTTPException(
            422,
            detail="pickup_window_end must be later than pickup_window_start",
        )

    end = payload.get("end_date")
    start = payload.get("start_date")
    if start and end and end < start:
        raise HTTPException(
            422,
            detail="end_date must be on or after start_date",
        )

    lead = payload.get("lead_time_days") or 0
    if lead < 1 or lead > 30:
        raise HTTPException(
            422,
            detail="lead_time_days must be between 1 and 30",
        )

    dom = payload.get("day_of_month")
    if dom is not None and (dom < 1 or dom > 31):
        raise HTTPException(422, detail="day_of_month must be between 1 and 31")

    mask = payload.get("day_of_week_mask") or 0
    if mask < 0 or mask > 0b1111111:
        raise HTTPException(
            422,
            detail="day_of_week_mask must fit 7 bits (Mon..Sun)",
        )


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MaterialisationStats:
    """Returned by materialise_due_occurrences for observability."""
    templates_evaluated: int
    occurrences_created: int
    occurrences_skipped_duplicate: int
    occurrences_skipped_capped: int


def _combine_date_time(d: date, t: time) -> datetime:
    """Return a naive UTC datetime — matches Order.earliest_delivery's column."""
    return datetime.combine(d, t)


def _resolve_external_ref(template: RecurringOrderTemplate, occurrence: date) -> str | None:
    if not template.external_ref_prefix:
        return None
    return f"{template.external_ref_prefix}-{occurrence.isoformat()}"


async def _create_order_for_occurrence(
    db: AsyncSession,
    template: RecurringOrderTemplate,
    occurrence: date,
) -> Order:
    earliest_delivery = _combine_date_time(occurrence, template.delivery_window_start)
    latest_delivery = _combine_date_time(occurrence, template.delivery_window_end)

    if (
        template.pickup_window_start is not None
        and template.pickup_window_end is not None
    ):
        earliest_pickup = _combine_date_time(occurrence, template.pickup_window_start)
        latest_pickup = _combine_date_time(occurrence, template.pickup_window_end)
    else:
        earliest_pickup = None
        latest_pickup = None

    custom_fields = dict(template.custom_fields or {})
    custom_fields["recurring_template_id"] = str(template.id)
    custom_fields["recurring_occurrence_date"] = occurrence.isoformat()

    is_ride = template.order_type == OrderType.RIDE

    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        tenant_id=template.tenant_id,
        type=template.order_type,
        status=OrderStatus.PENDING,
        priority=template.priority,
        pickup_address=template.pickup_address,
        pickup_lat=template.pickup_lat,
        pickup_lon=template.pickup_lon,
        delivery_address=template.delivery_address,
        delivery_lat=template.delivery_lat,
        delivery_lon=template.delivery_lon,
        depot_id=template.depot_id,
        weight_kg=(template.weight_kg or 0.0) if is_ride else template.weight_kg,
        is_fragile=template.is_fragile,
        is_temp_sensitive=template.is_temp_sensitive,
        earliest_pickup=earliest_pickup,
        latest_pickup=latest_pickup,
        earliest_delivery=earliest_delivery,
        latest_delivery=latest_delivery,
        customer_email=template.customer_email,
        customer_phone=template.customer_phone,
        tracking_token=create_tracking_token(str(order_id)),
        external_ref=_resolve_external_ref(template, occurrence),
        custom_fields=custom_fields,
        notes=template.notes,
        # ── Ride-specific fields (NULL/0 for non-RIDE templates) ──
        passenger_count=(template.passenger_count or 1) if is_ride else 1,
        passenger_name=template.passenger_name if is_ride else None,
        passenger_phone=template.passenger_phone if is_ride else None,
        flight_info=template.flight_info if is_ride else None,
        special_requirements=template.special_requirements if is_ride else None,
        pickup_buffer_minutes_override=(
            template.pickup_buffer_minutes_override if is_ride else None
        ),
    )

    # Attach tags pre-flush to dodge the async lazy-load that fails when
    # assigning a relationship after a flush (same pattern as create_order).
    if template.required_vehicle_tags:
        order.required_vehicle_tags = list(template.required_vehicle_tags)

    db.add(order)

    # Mirror the manual create_order behaviour: emit a status-history row so
    # that reporting queries treating "PENDING" as a transition land here too.
    db.add(
        OrderStatusHistory(
            order_id=order_id,
            from_status=None,
            to_status=OrderStatus.PENDING,
            actor_user_id=None,
            reason=f"Recurring template {template.name}",
        )
    )
    return order


async def _split_ride_occurrence_into_siblings(
    db: AsyncSession,
    *,
    parent: Order,
    template: RecurringOrderTemplate,
) -> list[Order]:
    """Split a materialised RIDE occurrence into N sibling orders.

    Mirrors `order_service._split_ride_into_siblings` but local-imports it
    to dodge the cyclic import (order_service → ... → recurring_order_service
    via webhook firehose). Sized using the template's
    `passengers_per_vehicle`, falling back to the tenant's ride policy.
    """
    if template.order_type != OrderType.RIDE or not template.allow_split:
        return []
    if parent.passenger_count is None or parent.passenger_count <= 0:
        return []

    per_vehicle = template.passengers_per_vehicle
    if per_vehicle is None or per_vehicle < 1:
        operations_policy = await operations_policy_service.get_operations_policy(
            db, template.tenant_id
        )
        per_vehicle = operations_policy.ride_settings.default_passengers_per_vehicle

    if parent.passenger_count <= per_vehicle:
        return []

    # Local import to avoid a circular module dependency at import time.
    from app.services.order_service import _split_ride_into_siblings  # noqa: PLC0415

    tag_ids = [tag.id for tag in (template.required_vehicle_tags or [])]
    return await _split_ride_into_siblings(
        db,
        parent=parent,
        per_vehicle=per_vehicle,
        tag_ids=tag_ids,
        operations_policy=None,
        actor_user_id=None,
    )


async def _existing_order_dates(
    db: AsyncSession, template_id: uuid.UUID
) -> set[date]:
    rows = (
        await db.execute(
            select(RecurringOrderOccurrence.occurrence_date).where(
                RecurringOrderOccurrence.template_id == template_id
            )
        )
    ).scalars().all()
    return set(rows)


async def materialise_template(
    db: AsyncSession,
    template: RecurringOrderTemplate,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Materialise due occurrences for a single template.

    Returns `(created, skipped_duplicate)`.  Caller commits.
    """
    now = now or utcnow_naive()
    today = now.date()
    horizon = today + timedelta(days=max(1, template.lead_time_days))

    occurrences = occurrences_in_range(
        template, range_start=today, range_end=horizon
    )
    if not occurrences:
        return 0, 0

    already = await _existing_order_dates(db, template.id)
    to_create = [d for d in occurrences if d not in already]
    if not to_create:
        return 0, len(occurrences)

    created = 0
    skipped_dup = 0
    for occurrence in to_create:
        savepoint = await db.begin_nested()
        try:
            order = await _create_order_for_occurrence(db, template, occurrence)
            await db.flush()
            # Split a large RIDE occurrence into siblings if the template
            # opted in. The first sibling is the just-created `order`; any
            # extras are persisted with the same `ride_group_id`.
            await _split_ride_occurrence_into_siblings(
                db, parent=order, template=template
            )
            db.add(
                RecurringOrderOccurrence(
                    template_id=template.id,
                    tenant_id=template.tenant_id,
                    order_id=order.id,
                    occurrence_date=occurrence,
                )
            )
            await db.flush()
            await savepoint.commit()
            created += 1
        except IntegrityError:
            await savepoint.rollback()
            skipped_dup += 1
            logger.info(
                "Recurring template %s: occurrence %s already exists, skipping.",
                template.id,
                occurrence,
            )

    template.occurrences_generated += created
    template.last_run_at = now
    if to_create:
        template.last_generated_for = max(template.last_generated_for or date.min, max(to_create))
    next_date = next_occurrence_after(template, today)
    template.next_generation_at = (
        datetime.combine(next_date, time.min) if next_date is not None else None
    )
    return created, skipped_dup


async def materialise_due_occurrences(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    now: datetime | None = None,
    template_ids: Iterable[uuid.UUID] | None = None,
) -> MaterialisationStats:
    """Walk every active template (optionally tenant- or id-scoped) and
    materialise due occurrences inside the per-template lookahead window.

    Idempotent: running this twice in quick succession is a no-op for any
    occurrence already realised. Caller is responsible for the surrounding
    `db.commit()` so a single celery beat run is one transaction.
    """
    now = now or utcnow_naive()
    today = now.date()

    filters = [
        RecurringOrderTemplate.is_active.is_(True),
        RecurringOrderTemplate.start_date <= today + timedelta(days=30),
        or_(
            RecurringOrderTemplate.end_date.is_(None),
            RecurringOrderTemplate.end_date >= today,
        ),
        or_(
            RecurringOrderTemplate.max_occurrences.is_(None),
            RecurringOrderTemplate.occurrences_generated
            < RecurringOrderTemplate.max_occurrences,
        ),
    ]
    if tenant_id is not None:
        filters.append(RecurringOrderTemplate.tenant_id == tenant_id)
    if template_ids is not None:
        ids = list(template_ids)
        if not ids:
            return MaterialisationStats(0, 0, 0, 0)
        filters.append(RecurringOrderTemplate.id.in_(ids))

    templates = (
        await db.execute(select(RecurringOrderTemplate).where(and_(*filters)))
    ).scalars().all()

    created = 0
    skipped_dup = 0
    skipped_cap = 0
    for tpl in templates:
        if (
            tpl.max_occurrences is not None
            and tpl.occurrences_generated >= tpl.max_occurrences
        ):
            skipped_cap += 1
            continue
        c, s = await materialise_template(db, tpl, now=now)
        created += c
        skipped_dup += s

    return MaterialisationStats(
        templates_evaluated=len(templates),
        occurrences_created=created,
        occurrences_skipped_duplicate=skipped_dup,
        occurrences_skipped_capped=skipped_cap,
    )
