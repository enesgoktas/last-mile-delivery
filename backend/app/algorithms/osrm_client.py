"""
Routing Client — Async HTTP interface to a self-hosted OSRM instance.

Uses the local OSRM Docker service for all routing operations. No external
API calls, no API keys, no rate limits. If OSRM is unreachable the request
fails loudly — there is no haversine fallback.

Provides:
- Single route queries (A→B distance + duration + polyline geometry)
- Single route queries with GeoJSON coordinates
- N×N distance/duration matrix via the OSRM Table API
- Nearest-road snap via the OSRM Nearest API
- Redis + local LRU caching of matrices (1-hour TTL)
- Retry logic with exponential backoff

All distances are returned in meters (int), durations in seconds (int).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

import httpx
import redis.asyncio as aioredis

from app.config import settings
from app.utils.perf_metrics import perf_metrics

logger = logging.getLogger(__name__)

_http_clients: dict[str, httpx.AsyncClient] = {}
_redis: aioredis.Redis | None = None
_local_matrix_cache: OrderedDict[str, tuple[list[list[int]], list[list[int]]]] = OrderedDict()
_local_matrix_cache_lock = asyncio.Lock()
_LOCAL_MATRIX_CACHE_MAX = 128

_LARGE_DISTANCE = 1_000_000_000   # 1,000,000 km — sentinel for unreachable
_LARGE_DURATION = 100_000_000     # ~3.2 years — sentinel for unreachable

_MAX_RETRIES = 2
_RETRY_DELAYS = [0.5, 1.0]

# How many OSRM /table sub-chunk requests to run concurrently when a problem
# exceeds OSRM_MAX_TABLE_SIZE. Each chunk is an independent HTTP round-trip;
# fanning them out drops matrix-build time from sum(latencies) to roughly
# max(latencies) × ceil(total / cap). Capped to keep the OSRM service
# responsive at large N — raise carefully if your OSRM instance has more
# threads to spare.
_OSRM_TABLE_CONCURRENCY = 8


# ---------------------------------------------------------------------------
# Data classes (unchanged signatures — callers depend on these)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RouteResult:
    """Single A→B route result."""
    distance_m: int
    duration_s: int
    geometry: str  # encoded polyline; empty string when unavailable


@dataclass(frozen=True, slots=True)
class RouteGeoJSONResult:
    """Single A→B route result with GeoJSON-compatible coordinates."""
    distance_m: int
    duration_s: int
    coordinates: list[list[float]]  # [[lon, lat], ...]


@dataclass(frozen=True, slots=True)
class MatrixResult:
    """N×N distance and duration matrices (integer values)."""
    distances: list[list[int]]   # meters
    durations: list[list[int]]   # seconds
    source: str                  # "osrm" or "cache"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _profile_base_url(profile: str) -> str:
    return settings.OSRM_PROFILE_BASE_URLS.get(profile, settings.OSRM_BASE_URL)


async def _get_http_client(profile: str = "driving") -> httpx.AsyncClient:
    base_url = _profile_base_url(profile)
    client = _http_clients.get(base_url)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                settings.OSRM_TIMEOUT_SECONDS,
                connect=5.0,
            ),
        )
        _http_clients[base_url] = client
    return client


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


def _matrix_cache_key(coords: list[tuple[float, float]], *, profile: str = "driving") -> str:
    canonical = [(round(lat, 5), round(lon, 5)) for lat, lon in coords]
    raw = json.dumps(
        {
            "cache_version": settings.DISTANCE_MATRIX_CACHE_VERSION,
            "profile": profile,
            "coords": canonical,
        },
        separators=(",", ":"),
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"dm:{settings.DISTANCE_MATRIX_CACHE_VERSION}:{profile}:{digest}"


def _coords_to_osrm_path(coords: list[tuple[float, float]]) -> str:
    """Convert [(lat, lon), ...] to OSRM URL path segment 'lon,lat;lon,lat;...'"""
    return ";".join(f"{lon},{lat}" for lat, lon in coords)


def _has_unreachable_block(distances: list[list[int]]) -> bool:
    n = len(distances)
    for i in range(n):
        non_diagonal = [distances[i][j] for j in range(n) if j != i]
        if non_diagonal and all(cell >= _LARGE_DISTANCE for cell in non_diagonal):
            return True
    return False


def _is_valid_matrix_payload(
    *,
    distances: list[list[int]],
    durations: list[list[int]],
    n: int,
) -> bool:
    if len(distances) != n or len(durations) != n:
        return False
    for row in distances:
        if len(row) != n:
            return False
    return all(len(row) == n for row in durations)


async def _read_local_matrix_cache(key: str) -> MatrixResult | None:
    async with _local_matrix_cache_lock:
        payload = _local_matrix_cache.get(key)
        if payload is None:
            return None
        _local_matrix_cache.move_to_end(key)
        distances, durations = payload
    return MatrixResult(distances=distances, durations=durations, source="cache")


async def _write_local_matrix_cache(
    key: str,
    *,
    distances: list[list[int]],
    durations: list[list[int]],
) -> None:
    async with _local_matrix_cache_lock:
        _local_matrix_cache[key] = (distances, durations)
        _local_matrix_cache.move_to_end(key)
        while len(_local_matrix_cache) > _LOCAL_MATRIX_CACHE_MAX:
            _local_matrix_cache.popitem(last=False)


async def clear_distance_matrix_local_cache() -> None:
    async with _local_matrix_cache_lock:
        _local_matrix_cache.clear()


# ---------------------------------------------------------------------------
# OSRM health probe
# ---------------------------------------------------------------------------

async def check_osrm_health() -> bool:
    """Return True if OSRM is reachable and serving routes.

    Uses the Nearest API with a known Istanbul coordinate as a lightweight
    smoke test. Does not raise — returns False on any failure.
    """
    try:
        client = await _get_http_client("driving")
        resp = await client.get("/nearest/v1/driving/29.0,41.0?number=1")
        return resp.status_code == 200 and resp.json().get("code") == "Ok"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# OSRM Route API — single A→B route
# ---------------------------------------------------------------------------

async def get_route(
    origin: tuple[float, float],
    destination: tuple[float, float],
    *,
    profile: str = "driving",
) -> RouteResult:
    """Get a single A→B route from OSRM.

    Origin and destination are (lat, lon) tuples.
    Raises RuntimeError if OSRM is unreachable after retries.
    """
    coords_path = _coords_to_osrm_path([origin, destination])
    url = f"/route/v1/{profile}/{coords_path}?overview=full&geometries=polyline"

    started = time.monotonic()
    client = await _get_http_client(profile)

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "Ok" or not data.get("routes"):
                raise ValueError(f"OSRM returned code={data.get('code')}")
            route = data["routes"][0]
            perf_metrics.observe_ms(
                "osrm_route_latency_ms", (time.monotonic() - started) * 1000
            )
            perf_metrics.increment("osrm_route_success")
            return RouteResult(
                distance_m=round(route["distance"]),
                duration_s=round(route["duration"]),
                geometry=route.get("geometry", ""),
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                logger.warning("OSRM route retry %d/%d: %s", attempt + 1, _MAX_RETRIES, exc)
                continue
            perf_metrics.increment("osrm_route_error")
            raise RuntimeError(f"OSRM route failed after {_MAX_RETRIES + 1} attempts: {exc}") from exc


async def get_route_geojson(
    origin: tuple[float, float],
    destination: tuple[float, float],
    *,
    profile: str = "driving",
) -> RouteGeoJSONResult:
    """Get a single A→B route from OSRM with GeoJSON coordinates.

    Used for drawing real road geometry on the dispatcher map.
    Raises RuntimeError if OSRM is unreachable after retries.
    """
    coords_path = _coords_to_osrm_path([origin, destination])
    url = f"/route/v1/{profile}/{coords_path}?overview=full&geometries=geojson"

    started = time.monotonic()
    client = await _get_http_client(profile)

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "Ok" or not data.get("routes"):
                raise ValueError(f"OSRM returned code={data.get('code')}")
            route = data["routes"][0]
            coordinates = route["geometry"]["coordinates"]
            if not coordinates or len(coordinates) < 2:
                raise ValueError("OSRM returned empty geometry")
            perf_metrics.observe_ms(
                "osrm_route_latency_ms", (time.monotonic() - started) * 1000
            )
            perf_metrics.increment("osrm_route_success")
            return RouteGeoJSONResult(
                distance_m=round(route["distance"]),
                duration_s=round(route["duration"]),
                coordinates=coordinates,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                logger.warning("OSRM geojson route retry %d/%d: %s", attempt + 1, _MAX_RETRIES, exc)
                continue
            perf_metrics.increment("osrm_route_error")
            raise RuntimeError(f"OSRM geojson route failed after {_MAX_RETRIES + 1} attempts: {exc}") from exc


# ---------------------------------------------------------------------------
# OSRM Nearest API — snap coordinate to nearest road
# ---------------------------------------------------------------------------

async def snap_to_road(lat: float, lon: float, *, profile: str = "driving") -> tuple[float, float]:
    """Snap a GPS coordinate to the nearest road segment via OSRM.

    Returns (lat, lon) of the snapped location.
    Raises RuntimeError if OSRM is unreachable after retries.
    """
    url = f"/nearest/v1/{profile}/{lon},{lat}?number=1"
    started = time.monotonic()
    client = await _get_http_client(profile)

    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "Ok" or not data.get("waypoints"):
                raise ValueError(f"OSRM nearest returned code={data.get('code')}")
            snapped = data["waypoints"][0]["location"]  # [lon, lat]
            perf_metrics.observe_ms(
                "osrm_nearest_latency_ms", (time.monotonic() - started) * 1000
            )
            perf_metrics.increment("osrm_nearest_success")
            return (snapped[1], snapped[0])
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                logger.warning("OSRM nearest retry %d/%d: %s", attempt + 1, _MAX_RETRIES, exc)
                continue
            perf_metrics.increment("osrm_nearest_error")
            raise RuntimeError(f"OSRM nearest failed after {_MAX_RETRIES + 1} attempts: {exc}") from exc


# ---------------------------------------------------------------------------
# OSRM Table API — N×N distance/duration matrix
# ---------------------------------------------------------------------------

async def _fetch_osrm_table(
    coords: list[tuple[float, float]],
    *,
    profile: str = "driving",
    sources: list[int] | None = None,
    destinations: list[int] | None = None,
) -> tuple[list[list[float | None]], list[list[float | None]]]:
    """Raw OSRM Table API call. Returns (distances, durations) as float matrices."""
    coords_path = _coords_to_osrm_path(coords)
    params = "annotations=distance,duration"
    if sources is not None:
        params += "&sources=" + ";".join(str(s) for s in sources)
    if destinations is not None:
        params += "&destinations=" + ";".join(str(d) for d in destinations)

    url = f"/table/v1/{profile}/{coords_path}?{params}"
    client = await _get_http_client(profile)
    resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "Ok":
        raise ValueError(f"OSRM table returned code={data.get('code')}")

    if "distances" not in data or "durations" not in data:
        raise ValueError(
            f"OSRM table response missing annotations "
            f"(keys={list(data.keys())}). Ensure annotations=distance,duration is set."
        )

    return data["distances"], data["durations"]


async def _fetch_full_table(
    coords: list[tuple[float, float]],
    *,
    profile: str = "driving",
) -> tuple[list[list[int]], list[list[int]]]:
    """Fetch a full N×N matrix, chunking if N > OSRM_MAX_TABLE_SIZE."""
    n = len(coords)
    max_size = settings.OSRM_MAX_TABLE_SIZE

    if n <= max_size:
        raw_dist, raw_dur = await _fetch_osrm_table(coords, profile=profile)
        distances = [
            [round(cell) if cell is not None else _LARGE_DISTANCE for cell in row]
            for row in raw_dist
        ]
        durations = [
            [round(cell) if cell is not None else _LARGE_DURATION for cell in row]
            for row in raw_dur
        ]
        return distances, durations

    # Chunk into sub-matrices.  For each chunk pair we send only the
    # union of source + destination coords to keep URLs short.
    distances = [[0] * n for _ in range(n)]
    durations = [[0] * n for _ in range(n)]

    # OSRM's --max-table-size caps the number of coordinates in a single
    # Table request.  For off-diagonal source/destination blocks we send the
    # union of both coordinate sets, so each side must be at most half the
    # limit.  Otherwise a 200-source block + 200-destination block becomes a
    # 400-coordinate request and fails only at production scale.
    chunk_size = max(1, max_size // 2)
    src_chunks = [list(range(i, min(i + chunk_size, n))) for i in range(0, n, chunk_size)]
    dst_chunks = [list(range(i, min(i + chunk_size, n))) for i in range(0, n, chunk_size)]

    # Each (src_chunk, dst_chunk) sub-table call is an independent OSRM HTTP
    # request — they share no state. The original serial nested loop stacked
    # all (n_src_chunks × n_dst_chunks) latencies; for a 1325-node problem
    # with max_size=200 (chunk_size=100) that's ~196 serial round-trips per
    # profile. Fan them out with `asyncio.gather` and a concurrency cap so we
    # don't slam OSRM. The cap keeps the OSRM service responsive at large N
    # while still saturating its parallelism for typical sizes.
    sem = asyncio.Semaphore(_OSRM_TABLE_CONCURRENCY)

    async def _fetch_chunk(src_idxs: list[int], dst_idxs: list[int]):
        async with sem:
            all_idxs = sorted(set(src_idxs) | set(dst_idxs))
            subset_coords = [coords[i] for i in all_idxs]
            idx_remap = {orig: new for new, orig in enumerate(all_idxs)}
            raw_dist, raw_dur = await _fetch_osrm_table(
                subset_coords,
                profile=profile,
                sources=[idx_remap[s] for s in src_idxs],
                destinations=[idx_remap[d] for d in dst_idxs],
            )
            return src_idxs, dst_idxs, raw_dist, raw_dur

    chunk_results = await asyncio.gather(
        *[_fetch_chunk(src, dst) for src in src_chunks for dst in dst_chunks]
    )
    for src_idxs, dst_idxs, raw_dist, raw_dur in chunk_results:
        for si, src in enumerate(src_idxs):
            for di, dst in enumerate(dst_idxs):
                d = raw_dist[si][di]
                t = raw_dur[si][di]
                distances[src][dst] = round(d) if d is not None else _LARGE_DISTANCE
                durations[src][dst] = round(t) if t is not None else _LARGE_DURATION

    return distances, durations


async def get_distance_matrix(
    coords: list[tuple[float, float]],
    *,
    profile: str = "driving",
    use_cache: bool = True,
) -> MatrixResult:
    """Get an N×N distance + duration matrix from OSRM.

    Caches results in Redis (1-hour TTL) and a local LRU cache.
    Raises RuntimeError if OSRM is unreachable after retries.

    Args:
        coords:    List of (lat, lon) tuples — all unique locations for the
                   current optimization problem (depots + stop points).
        use_cache: Whether to consult and populate the Redis cache.

    Returns:
        MatrixResult with integer distances (m) and durations (s).
    """
    n = len(coords)
    started = time.monotonic()

    if n == 0:
        return MatrixResult(distances=[], durations=[], source="empty")
    if n == 1:
        return MatrixResult(distances=[[0]], durations=[[0]], source="trivial")

    cache_key = _matrix_cache_key(coords, profile=profile)
    if use_cache:
        local_cached = await _read_local_matrix_cache(cache_key)
        if local_cached is not None:
            if _has_unreachable_block(local_cached.distances):
                logger.warning("Local matrix cache had unreachable block; ignoring.")
            else:
                return local_cached

        try:
            r = await _get_redis()
            cached = await r.get(cache_key)
            if cached:
                data = json.loads(cached)
                distances = data["distances"]
                durations = data["durations"]
                if not _is_valid_matrix_payload(distances=distances, durations=durations, n=n):
                    raise ValueError("Cached matrix shape mismatch")
                if _has_unreachable_block(distances):
                    raise ValueError("Cached matrix contains unreachable block")
                await _write_local_matrix_cache(cache_key, distances=distances, durations=durations)
                logger.info("Distance matrix cache hit (%d×%d, profile=%s)", n, n, profile)
                perf_metrics.increment("osrm_table_cache_hit")
                perf_metrics.observe_ms(
                    "osrm_table_latency_ms", (time.monotonic() - started) * 1000
                )
                return MatrixResult(distances=distances, durations=durations, source="cache")
        except Exception as exc:
            logger.warning("Redis cache read failed: %s", exc)

    for attempt in range(_MAX_RETRIES + 1):
        try:
            distances, durations = await _fetch_full_table(coords, profile=profile)

            if not _is_valid_matrix_payload(distances=distances, durations=durations, n=n):
                raise ValueError("OSRM matrix response has unexpected shape")

            if _has_unreachable_block(distances):
                logger.warning(
                    "OSRM matrix contains unreachable block — some locations "
                    "may not be routable. Proceeding with sentinel values."
                )

            result = MatrixResult(distances=distances, durations=durations, source="osrm")
            perf_metrics.increment("osrm_table_success")
            perf_metrics.observe_ms(
                "osrm_table_latency_ms", (time.monotonic() - started) * 1000
            )

            if use_cache:
                await _write_local_matrix_cache(
                    cache_key, distances=distances, durations=durations
                )
                try:
                    r = await _get_redis()
                    payload = json.dumps({"distances": distances, "durations": durations})
                    await r.set(cache_key, payload, ex=settings.DISTANCE_MATRIX_CACHE_TTL)
                except Exception as exc:
                    logger.warning("Redis cache write failed: %s", exc)

            return result

        except (httpx.HTTPError, KeyError, ValueError) as exc:
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                logger.warning(
                    "OSRM matrix retry %d/%d: %s", attempt + 1, _MAX_RETRIES, exc
                )
                continue
            perf_metrics.increment("osrm_table_error")
            raise RuntimeError(
                f"OSRM matrix failed after {_MAX_RETRIES + 1} attempts: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Metrics snapshot (used by monitoring)
# ---------------------------------------------------------------------------

def get_osrm_metrics_snapshot() -> dict[str, float | int | None]:
    return {
        "osrm_table_p50_ms": perf_metrics.percentile("osrm_table_latency_ms", 0.5),
        "osrm_table_p95_ms": perf_metrics.percentile("osrm_table_latency_ms", 0.95),
        "osrm_route_p50_ms": perf_metrics.percentile("osrm_route_latency_ms", 0.5),
        "osrm_route_p95_ms": perf_metrics.percentile("osrm_route_latency_ms", 0.95),
        "osrm_table_error_count": perf_metrics.get_counter("osrm_table_error"),
        "osrm_table_success_count": perf_metrics.get_counter("osrm_table_success"),
        "osrm_table_cache_hit_count": perf_metrics.get_counter("osrm_table_cache_hit"),
        "osrm_route_error_count": perf_metrics.get_counter("osrm_route_error"),
        "osrm_route_success_count": perf_metrics.get_counter("osrm_route_success"),
    }


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

async def close() -> None:
    """Shutdown HTTP client and Redis connection pool."""
    global _redis
    for client in _http_clients.values():
        if not client.is_closed:
            await client.aclose()
    _http_clients.clear()
    if _redis:
        await _redis.close()
        _redis = None
    await clear_distance_matrix_local_cache()
