"""Microbenchmark for `_load_multiplier_lookup` latency.

Measures the additional latency that the new prior-fallback query adds
to the multiplier-lookup hot path. Runs N iterations against a real DB
with N geohash sets of different sizes, both with `TRAFFIC_PRIOR_ENABLED`
True and False, and reports p50/p95/p99.

Goal: confirm the prior lookup adds ≤ 10ms (p95) on a typical 200-cell
dispatch matrix size.

Usage (from backend/):

    python -m scripts.bench_multiplier_lookup [--iterations 200]
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import app.models.vehicle_tag  # noqa: F401
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.traffic_cell_prior import TrafficCellPrior
from app.services import traffic_profile_service

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
DEFAULT_SIZES = (10, 50, 100, 200)
DEFAULT_ITERATIONS = 200


async def _sample_geohashes(db, n: int) -> list[str]:
    """Pull N real geohash6 strings from the prior table to make queries realistic."""
    result = await db.execute(
        select(TrafficCellPrior.geohash6)
        .distinct()
        .where(TrafficCellPrior.region_code == settings.TRAFFIC_PRIOR_ACTIVE_REGION_CODE)
        .limit(n)
    )
    rows = result.all()
    return [row[0] for row in rows]


def _percentile(samples: list[float], q: float) -> float:
    s = sorted(samples)
    idx = int(q * (len(s) - 1))
    return s[idx]


async def _measure(
    db,
    geohashes: list[str],
    *,
    iterations: int,
    prior_enabled: bool,
) -> dict[str, float]:
    settings.TRAFFIC_PRIOR_ENABLED = prior_enabled
    geohash_set = set(geohashes)
    at_time = datetime(2026, 5, 26, 18, 0, tzinfo=ISTANBUL_TZ)

    latencies_ms: list[float] = []
    for _ in range(iterations):
        # Bypass the in-process LRU each call — we want to measure the cold
        # path (DB query), not the cache-hit fast path.
        traffic_profile_service.invalidate_lookup_cache()
        t0 = time.perf_counter()
        await traffic_profile_service._load_multiplier_lookup(
            db,
            geohashes=geohash_set,
            at_time=at_time,
        )
        latencies_ms.append((time.perf_counter() - t0) * 1000)
    return {
        "n": len(geohashes),
        "iterations": iterations,
        "prior_enabled": prior_enabled,
        "p50_ms": _percentile(latencies_ms, 0.5),
        "p95_ms": _percentile(latencies_ms, 0.95),
        "p99_ms": _percentile(latencies_ms, 0.99),
        "min_ms": min(latencies_ms),
        "max_ms": max(latencies_ms),
        "mean_ms": statistics.mean(latencies_ms),
    }


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    p.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    args = p.parse_args()

    async with async_session() as db:
        max_size = max(args.sizes)
        all_hashes = await _sample_geohashes(db, max_size)
        if len(all_hashes) < max_size:
            print(
                f"WARNING: only {len(all_hashes)} geohashes available in TrafficCellPrior; "
                f"requested max size {max_size}. Truncating."
            )
            args.sizes = [s for s in args.sizes if s <= len(all_hashes)]

        rows = []
        for n in args.sizes:
            sample = all_hashes[:n]
            print(f"Benchmarking N={n} with {args.iterations} iterations...")
            on = await _measure(db, sample, iterations=args.iterations, prior_enabled=True)
            off = await _measure(db, sample, iterations=args.iterations, prior_enabled=False)
            rows.append(
                {
                    "n": n,
                    "on_p50": on["p50_ms"],
                    "off_p50": off["p50_ms"],
                    "on_p95": on["p95_ms"],
                    "off_p95": off["p95_ms"],
                    "on_p99": on["p99_ms"],
                    "off_p99": off["p99_ms"],
                    "delta_p95_ms": on["p95_ms"] - off["p95_ms"],
                }
            )

    # Restore default
    settings.TRAFFIC_PRIOR_ENABLED = True
    traffic_profile_service.invalidate_lookup_cache()

    # Report
    print()
    print(f"{'N':>5} | {'OFF p50':>9} {'ON p50':>9} | {'OFF p95':>9} {'ON p95':>9} | {'OFF p99':>9} {'ON p99':>9} | {'Δp95 (ms)':>12}")
    print("-" * 100)
    for r in rows:
        print(
            f"{r['n']:>5} | {r['off_p50']:>7.2f}ms {r['on_p50']:>7.2f}ms | "
            f"{r['off_p95']:>7.2f}ms {r['on_p95']:>7.2f}ms | "
            f"{r['off_p99']:>7.2f}ms {r['on_p99']:>7.2f}ms | "
            f"{r['delta_p95_ms']:>+10.2f}"
        )
    print()
    worst_delta = max(r["delta_p95_ms"] for r in rows)
    threshold = 10.0
    verdict = "PASS" if worst_delta <= threshold else "FAIL"
    print(f"Verdict: {verdict}  (worst Δp95 = {worst_delta:+.2f} ms; threshold = {threshold:.1f} ms)")


if __name__ == "__main__":
    asyncio.run(main())
