"""One-shot CLI: ingest IBB hourly traffic CSVs into `traffic_cell_priors`.

Usage (from backend/):

    python -m scripts.backfill_ibb_traffic_priors \\
        --csv-glob '/var/data/ibb/*.csv' \\
        --region-code IST \\
        --source-first-seen 2020-01-01 \\
        --source-last-seen 2025-12-31 \\
        [--dry-run]

The script:

  1. Verifies each CSV's GEOHASH column matches `app.utils.geo.encode_geohash`
     on the (LATITUDE, LONGITUDE) of the same row. Halts on any mismatch
     before doing any DB writes — we'd rather learn about a hash format
     drift here than mid-dispatch.

  2. Runs the DuckDB aggregation in `traffic_prior_service.ingest_ibb_csv`,
     streaming results in chunks and upserting via ON CONFLICT.

  3. Prints a JSON-shaped summary to stdout for log capture.

`--dry-run` runs the aggregation and prints what would be written, but
rolls back instead of committing.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import json
import sys
from datetime import date
from pathlib import Path

from app.database import async_session
from app.services import traffic_prior_service
from app.utils.geo import encode_geohash

_SANITY_ROWS_PER_FILE = 10


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv-glob", required=True, help="Glob pattern matching IBB CSVs.")
    p.add_argument("--region-code", default="IST")
    p.add_argument("--source-first-seen", required=True, type=date.fromisoformat)
    p.add_argument("--source-last-seen", required=True, type=date.fromisoformat)
    p.add_argument("--source-name", default="IBB")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Aggregate but do not commit; roll back at the end.",
    )
    return p.parse_args(argv)


def _resolve_paths(pattern: str) -> list[Path]:
    matched = sorted(glob.glob(pattern))
    paths = [Path(p) for p in matched]
    if not paths:
        raise SystemExit(f"No CSVs matched glob: {pattern!r}")
    return paths


def _check_geohash_consistency(path: Path) -> None:
    with path.open() as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= _SANITY_ROWS_PER_FILE:
                break
            try:
                lat = float(row["LATITUDE"])
                lon = float(row["LONGITUDE"])
                expected = row["GEOHASH"].strip().lower()
            except (KeyError, ValueError) as exc:
                raise SystemExit(
                    f"{path}: row {i} has malformed lat/lon/geohash: {exc}"
                ) from exc
            actual = encode_geohash(lat, lon, precision=6)
            if actual != expected:
                raise SystemExit(
                    f"{path}: row {i} geohash mismatch — "
                    f"IBB says {expected!r}, encode_geohash says {actual!r}. "
                    "Halting before any DB writes."
                )


async def _run(args: argparse.Namespace) -> dict:
    paths = _resolve_paths(args.csv_glob)
    print(f"[backfill] {len(paths)} CSV file(s) matched glob {args.csv_glob!r}", file=sys.stderr)
    for path in paths:
        _check_geohash_consistency(path)
    print("[backfill] geohash sanity check passed on all files", file=sys.stderr)

    async with async_session() as db:
        try:
            summary = await traffic_prior_service.ingest_ibb_csv(
                db,
                csv_paths=paths,
                region_code=args.region_code,
                source_first_seen=args.source_first_seen,
                source_last_seen=args.source_last_seen,
                source_name=args.source_name,
            )
            if args.dry_run:
                await db.rollback()
                print("[backfill] --dry-run: rolled back", file=sys.stderr)
            return {
                "region_code": summary.region_code,
                "csv_files": len(summary.csv_paths),
                "rows_processed": summary.rows_processed,
                "extrapolated_buckets": summary.extrapolated_buckets,
                "duration_seconds": round(summary.duration_seconds, 2),
                "dry_run": args.dry_run,
            }
        except Exception:
            await db.rollback()
            raise


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    result = asyncio.run(_run(args))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
