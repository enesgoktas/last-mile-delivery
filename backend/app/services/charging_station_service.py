"""Open Charge Map sync + tenant charger lookups (Phase 2.3).

Architecturally, OCM is a *catalogue source*, not a runtime dependency.
The sync periodically (weekly by default — see celery_app beat schedule)
upserts platform-shared `ChargingStation` rows (tenant_id NULL,
provider='OCM') into our DB. After that the dispatch pipeline reads
exclusively from our `charging_stations` table; OCM downtime never
blocks routing.

Per-tenant rows (provider='MANUAL') are managed via the API and
untouched here.

OCM API:
    GET https://api.openchargemap.io/v3/poi
        ?countrycode=TR
        &maxresults=10000
        &compact=false
        &verbose=false
        &output=json
    Header: X-API-Key: <key>

The free tier covers Turkey comfortably in one call; if the country
ever exceeds 10000 stations we'd need paged calls (no logic for that
yet — checked: ~5000 stations as of mid-2025).

Connector type normalisation: OCM's `Connections[].ConnectionType.Title`
is a free-text label like "CCS (Type 2)", "CHAdeMO", "Type 2 (Socket
Only)". We normalise to a small canonical set: CCS / CHAdeMO / Type2 /
Type1 / NACS / OTHER.
"""
from __future__ import annotations

import logging
import math
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Iterable

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.charging_station import ChargingStation
from app.utils.compat import utcnow_naive
from app.utils.tenant_context import get_current_tenant_id

logger = logging.getLogger(__name__)

# Skip-tenant-scope marker for queries that intentionally read across all
# tenants (platform-shared rows with NULL tenant_id wouldn't match the
# auto-applied filter otherwise).
_X = {"skip_tenant_scope": True}


_CONNECTOR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bccs\b", re.I), "CCS"),
    (re.compile(r"\bchademo\b", re.I), "CHAdeMO"),
    (re.compile(r"\bnacs\b|\btesla\b", re.I), "NACS"),
    (re.compile(r"\btype\s*2\b|\bmennekes\b", re.I), "Type2"),
    (re.compile(r"\btype\s*1\b|\bj1772\b", re.I), "Type1"),
]


def _normalise_connector(label: str | None) -> str:
    if not label:
        return "OTHER"
    for pattern, canonical in _CONNECTOR_PATTERNS:
        if pattern.search(label):
            return canonical
    return "OTHER"


def _connector_set_from_ocm(connections: list[dict[str, Any]] | None) -> tuple[list[str], int, bool]:
    """Returns (sorted_connector_types, max_kw, is_dc)."""
    if not connections:
        return ([], 0, False)
    types: set[str] = set()
    max_kw = 0
    is_dc = False
    for c in connections:
        ctype = c.get("ConnectionType") or {}
        types.add(_normalise_connector(ctype.get("Title")))
        kw = c.get("PowerKW") or 0
        try:
            max_kw = max(max_kw, int(kw))
        except (TypeError, ValueError):
            pass
        # OCM doesn't expose an explicit IsDC flag; Title is "DC" for
        # direct-current and "AC (...)" for AC. Description is more
        # robust ("Direct Current" / "Alternating Current ..."). Either
        # one suffices.
        current = c.get("CurrentType") or {}
        title = (current.get("Title") or "").strip().upper()
        desc = (current.get("Description") or "").strip().upper()
        if title == "DC" or desc.startswith("DIRECT"):
            is_dc = True
    return (sorted(types), max_kw, is_dc)


# ── Sync ──────────────────────────────────────────────────────────────


async def refresh_open_charge_map(db: AsyncSession) -> dict:
    """Pull Turkey stations from OCM and upsert platform-shared rows.

    Returns a summary dict with `fetched`, `inserted`, `updated`,
    `marked_inactive`, `error` (if any). Safe to call manually via the
    superadmin endpoint or as a daily Celery beat task.
    """
    api_key = settings.OPEN_CHARGE_MAP_API_KEY.strip()
    if not api_key:
        logger.warning(
            "OPEN_CHARGE_MAP_API_KEY not configured — Open Charge Map sync skipped. "
            "Auto-charging dispatch will use platform-seeded + tenant-MANUAL "
            "stations only."
        )
        return {
            "fetched": 0,
            "inserted": 0,
            "updated": 0,
            "marked_inactive": 0,
            "error": "OPEN_CHARGE_MAP_API_KEY not configured",
        }

    url = f"{settings.OPEN_CHARGE_MAP_BASE_URL.rstrip('/')}/poi"
    params = {
        "countrycode": settings.OPEN_CHARGE_MAP_COUNTRY_CODE,
        "maxresults": settings.OPEN_CHARGE_MAP_SYNC_PAGE_SIZE,
        # `compact=false` is needed to receive the nested ConnectionType
        # and OperatorInfo objects we read for connector / operator data.
        # `verbose=false` strips media + comments we don't need, keeping
        # the response under a few MB for ~5000 stations.
        "compact": "false",
        "verbose": "false",
        "output": "json",
    }
    headers = {"X-API-Key": api_key}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            pois = response.json()
    except httpx.HTTPError as exc:
        logger.exception("Open Charge Map fetch failed")
        return {
            "fetched": 0,
            "inserted": 0,
            "updated": 0,
            "marked_inactive": 0,
            "error": f"OCM HTTP error: {exc}",
        }

    if not isinstance(pois, list):
        return {
            "fetched": 0,
            "inserted": 0,
            "updated": 0,
            "marked_inactive": 0,
            "error": "Unexpected OCM response shape (expected list)",
        }

    now = utcnow_naive()
    seen_external_ids: set[str] = set()
    inserted = 0
    updated = 0

    for poi in pois:
        external_id = str(poi.get("ID") or "").strip()
        if not external_id:
            continue
        seen_external_ids.add(external_id)

        addr_info = poi.get("AddressInfo") or {}
        lat = addr_info.get("Latitude")
        lon = addr_info.get("Longitude")
        if lat is None or lon is None:
            continue

        connectors, max_kw, is_dc = _connector_set_from_ocm(poi.get("Connections"))
        operator_info = poi.get("OperatorInfo") or {}
        status_info = poi.get("StatusType") or {}

        # Marked operational by OCM. NULL/undefined -> assume operational.
        is_operational = status_info.get("IsOperational")
        is_active = is_operational is None or bool(is_operational)

        existing_q = select(ChargingStation).where(
            ChargingStation.provider == "OCM",
            ChargingStation.external_id == external_id,
            ChargingStation.tenant_id.is_(None),
        )
        existing = (await db.execute(existing_q, execution_options=_X)).scalar_one_or_none()

        if existing is None:
            row = ChargingStation(
                id=uuid.uuid4(),
                tenant_id=None,
                provider="OCM",
                external_id=external_id,
                name=str(addr_info.get("Title") or addr_info.get("AddressLine1") or "Unnamed station")[:255],
                operator=(operator_info.get("Title") or None),
                lat=float(lat),
                lon=float(lon),
                address=str(addr_info.get("AddressLine1") or "")[:500] or None,
                province_code=None,  # OCM doesn't carry TR province codes
                country_code="TR",
                connector_types=connectors,
                max_kw=max_kw,
                is_dc=is_dc,
                is_24_7=True,  # OCM doesn't expose hours reliably; default open
                operating_hours=None,
                is_active=is_active,
                last_verified_at=now,
                last_synced_at=now,
            )
            db.add(row)
            inserted += 1
        else:
            existing.name = str(addr_info.get("Title") or existing.name)[:255]
            existing.operator = operator_info.get("Title") or existing.operator
            existing.lat = float(lat)
            existing.lon = float(lon)
            existing.address = (str(addr_info.get("AddressLine1") or "")[:500] or existing.address)
            existing.connector_types = connectors
            existing.max_kw = max_kw
            existing.is_dc = is_dc
            existing.is_active = is_active
            existing.last_verified_at = now
            existing.last_synced_at = now
            updated += 1

    # Mark stale OCM rows inactive — those not seen for STALE_DAYS.
    cutoff = now - timedelta(days=settings.OPEN_CHARGE_MAP_STALE_DAYS)
    stale_q = select(ChargingStation).where(
        ChargingStation.provider == "OCM",
        ChargingStation.tenant_id.is_(None),
        ChargingStation.is_active.is_(True),
        or_(
            ChargingStation.last_synced_at.is_(None),
            ChargingStation.last_synced_at < cutoff,
        ),
    )
    stale_rows = (await db.execute(stale_q, execution_options=_X)).scalars().all()
    marked_inactive = 0
    for row in stale_rows:
        if row.external_id in seen_external_ids:
            continue
        row.is_active = False
        marked_inactive += 1

    await db.flush()
    summary = {
        "fetched": len(pois),
        "inserted": inserted,
        "updated": updated,
        "marked_inactive": marked_inactive,
        "error": None,
    }
    logger.info("Open Charge Map sync complete: %s", summary)
    return summary


# ── Tenant-facing lookups ─────────────────────────────────────────────


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Rough great-circle distance in km. Good enough for ranking
    candidates within a few-km radius (where the curvature of the
    Earth doesn't matter much for relative comparison).
    """
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def list_stations_for_tenant(
    db: AsyncSession,
    principal,
    *,
    only_active: bool = True,
) -> list[ChargingStation]:
    """Platform-shared rows + the calling tenant's own MANUAL rows.

    Platform-shared rows have `tenant_id IS NULL` so the global ORM
    tenant filter would exclude them — bypass via `skip_tenant_scope`
    and apply the (tenant_id IS NULL OR tenant_id = :me) clause manually.
    """
    tenant_id = get_current_tenant_id(principal)
    query = select(ChargingStation).where(
        or_(
            ChargingStation.tenant_id.is_(None),
            ChargingStation.tenant_id == tenant_id,
        )
    )
    if only_active:
        query = query.where(ChargingStation.is_active.is_(True))
    result = await db.execute(query, execution_options=_X)
    return list(result.scalars().all())


def filter_compatible_chargers(
    stations: Iterable[ChargingStation],
    *,
    supported_connectors: list[str] | None,
    allowed_networks: list[str] | None,
    min_kw: int,
) -> list[ChargingStation]:
    """Apply connector / network / power filtering to a station list."""
    supported = {c.upper() for c in supported_connectors or []}
    allowed = {n.lower() for n in allowed_networks or []} if allowed_networks else None

    out: list[ChargingStation] = []
    for s in stations:
        if not s.is_active:
            continue
        if min_kw and s.max_kw < min_kw:
            continue
        # Connector intersection. Empty supported list means "any".
        if supported:
            if not any(c.upper() in supported for c in (s.connector_types or [])):
                continue
        if allowed is not None:
            if not s.operator or s.operator.lower() not in allowed:
                continue
        out.append(s)
    return out


def candidate_chargers_near(
    stations: Iterable[ChargingStation],
    *,
    points: list[tuple[float, float]],
    radius_km: float,
    top_n: int = 3,
) -> list[ChargingStation]:
    """Return up to `top_n` stations whose nearest distance to any of the
    given (lat, lon) waypoints is ≤ `radius_km`, sorted by closeness.

    Caller passes the route's depot + cluster centroids; this keeps the
    candidate set small enough for the solver matrix.
    """
    if not points:
        return []
    scored: list[tuple[float, ChargingStation]] = []
    for s in stations:
        nearest_km = min(
            _haversine_km(s.lat, s.lon, lat, lon) for lat, lon in points
        )
        if nearest_km <= radius_km:
            scored.append((nearest_km, s))
    scored.sort(key=lambda t: t[0])
    return [s for _, s in scored[:top_n]]


def estimate_charge_time_seconds(
    *,
    current_pct: int,
    target_pct: int,
    battery_kwh: float,
    charger_kw: float,
) -> int:
    """Energy delta / charger power, with a 10% taper-loss padding.

    Real-world charge curves slow above ~80%; we plan to target_pct only
    and add headroom rather than modelling the curve. Returns seconds.
    """
    delta_pct = max(0, int(target_pct) - int(current_pct))
    if delta_pct == 0 or battery_kwh <= 0 or charger_kw <= 0:
        return 0
    energy_kwh = battery_kwh * (delta_pct / 100.0)
    hours = energy_kwh / charger_kw
    return int(hours * 3600 * 1.1)  # 10% padding for taper / handshake
