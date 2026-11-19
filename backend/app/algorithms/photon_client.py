"""Photon geocoding client."""
from __future__ import annotations

import logging
from typing import TypedDict

import httpx

from app.config import settings

_http_client: httpx.AsyncClient | None = None
logger = logging.getLogger(__name__)

# Istanbul city centre — used as location bias for search results
_ISTANBUL_LAT = 41.0082
_ISTANBUL_LON = 28.9784


class GeocodeResult(TypedDict):
    display_name: str
    lat: float
    lon: float


def _build_display_name(props: dict) -> str:
    """Construct a human-readable address from Photon feature properties."""
    parts: list[str] = []
    name = props.get("name", "")
    street = props.get("street", "")
    housenumber = props.get("housenumber", "")
    city = props.get("city", "")
    country = props.get("country", "")

    if name:
        parts.append(name)

    if street:
        street_part = f"{street} {housenumber}".strip() if housenumber else street
        # Avoid repeating the name when it is the same as the street
        if street_part != name:
            parts.append(street_part)

    if city and city != name:
        parts.append(city)

    if country and country != name:
        parts.append(country)

    return ", ".join(p for p in parts if p) or "Unknown location"


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            base_url=settings.PHOTON_BASE_URL,
            timeout=httpx.Timeout(8.0, connect=4.0),
            headers={"User-Agent": "LastMileDelivery/1.0 (geocoding; contact@delivery.local)"},
        )
    return _http_client


async def search_addresses(query: str, limit: int = 5) -> list[GeocodeResult]:
    """Return up to *limit* geocoded address suggestions for *query*.

    Results are biased toward Istanbul using Photon's lat/lon parameters.
    """
    client = await _get_client()
    try:
        response = await client.get(
            "/api",
            params={
                "q": query,
                "limit": limit,
                "lat": _ISTANBUL_LAT,
                "lon": _ISTANBUL_LON,
            },
        )
        response.raise_for_status()
        features = response.json().get("features") or []
        results: list[GeocodeResult] = []
        for feature in features:
            coords = feature.get("geometry", {}).get("coordinates")
            if not coords or len(coords) < 2:
                continue
            lon, lat = float(coords[0]), float(coords[1])
            props = feature.get("properties") or {}
            results.append(
                GeocodeResult(
                    display_name=_build_display_name(props),
                    lat=lat,
                    lon=lon,
                )
            )
        return results
    except Exception as exc:
        logger.warning("Photon search request failed: %s", exc)
        return []


async def geocode_address(address: str) -> tuple[float, float] | None:
    """Geocode a single address string, returning (lat, lon) or None."""
    results = await search_addresses(address, limit=1)
    if not results:
        return None
    return results[0]["lat"], results[0]["lon"]


async def close() -> None:
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None
