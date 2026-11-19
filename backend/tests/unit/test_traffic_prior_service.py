from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from app.services import traffic_prior_service


def _write_csv(tmp_path: Path, name: str, rows: list[tuple]) -> Path:
    header = (
        '"DATE_TIME","LATITUDE","LONGITUDE","GEOHASH",'
        '"MINIMUM_SPEED","MAXIMUM_SPEED","AVERAGE_SPEED","NUMBER_OF_VEHICLES"\n'
    )
    body_lines = []
    for dt, lat, lon, gh, mn, mx, avg, n in rows:
        body_lines.append(
            f'"{dt}","{lat}","{lon}",{gh},{mn},{mx},{avg},{n}'
        )
    path = tmp_path / name
    path.write_text(header + "\n".join(body_lines) + "\n")
    return path


def _make_synthetic_csv(tmp_path: Path) -> Path:
    """Two cells: one well-observed (free-flow defined per-cell), one sparse.

    Cell 'sxk00a' gets 200 observations across hours — its P85 is its own value.
    Cell 'sxk00b' gets just 3 observations — should be flagged extrapolated.
    """
    rows = []
    # Well-observed cell: 168 buckets × 1 row each + extra fill = 200 rows.
    # All speeds 60 km/h except a few "free-flow" hours at 90.
    for dow in range(7):
        for hr in range(24):
            speed = 90 if (dow, hr) in {(0, 3), (1, 3), (2, 3)} else 60
            rows.append((
                f"2024-01-0{(dow % 7) + 1} {hr:02d}:00:00",
                40.97, 29.20, "sxk00a", speed - 10, speed + 10, speed, 25,
            ))
    # Extra fill rows for sxk00a — repeat the same observations.
    for i in range(32):
        rows.append((
            f"2024-02-0{(i % 9) + 1} 12:00:00",
            40.97, 29.20, "sxk00a", 50, 70, 60, 25,
        ))
    # Sparse cell: only 3 observations.
    for hr in [8, 12, 18]:
        rows.append((
            f"2024-01-01 {hr:02d}:00:00",
            41.00, 29.00, "sxk00b", 40, 60, 50, 5,
        ))
    return _write_csv(tmp_path, "synthetic.csv", rows)


def test_compute_free_flow_well_observed_cell(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    csv_path = _make_synthetic_csv(tmp_path)
    duck = duckdb.connect(":memory:")
    try:
        per_cell, city_p85 = traffic_prior_service.compute_free_flow(
            duck, csv_paths=[str(csv_path)], min_cell_rows=50,
        )
    finally:
        duck.close()

    # Well-observed cell sxk00a — has rows >= 50 and free-flow P85 should be
    # in the 60-90 range. Clamped to [10, 110].
    assert "sxk00a" in per_cell
    assert 50.0 <= per_cell["sxk00a"] <= 110.0

    # Sparse cell sxk00b: only 3 rows, below min_cell_rows. Should be absent
    # from per_cell dict (caller falls back to city_p85).
    assert "sxk00b" not in per_cell

    # City-wide P85 is also in a sensible range.
    assert 10.0 <= city_p85 <= 110.0


class _FakeExecute:
    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self


class _FakeRow:
    def __init__(self, geohash6, prior_multiplier):
        self._tuple = (geohash6, prior_multiplier)

    def __iter__(self):
        return iter(self._tuple)

    def __getitem__(self, idx):
        return self._tuple[idx]


class _FakeQueryResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.commit_calls = 0
        self.execute_calls = 0
        self.last_statement = None

    async def execute(self, stmt):
        self.execute_calls += 1
        self.last_statement = stmt
        return _FakeQueryResult(self._rows)

    async def commit(self):
        self.commit_calls += 1


@pytest.mark.asyncio
async def test_lookup_prior_multipliers_returns_clamped_dict():
    fake_db = _FakeSession(
        [
            _FakeRow("sxk00a", 1.5),
            _FakeRow("sxk00b", 2.5),  # over-max → must clamp to 1.8
            _FakeRow("sxk00c", 0.1),  # under-min → must clamp to 0.6
        ]
    )
    result = await traffic_prior_service.lookup_prior_multipliers(
        fake_db,
        region_code="IST",
        geohashes={"sxk00a", "sxk00b", "sxk00c"},
        day_of_week=1,
        hour_bucket=17,
    )
    assert result["sxk00a"] == pytest.approx(1.5)
    assert result["sxk00b"] == pytest.approx(1.8)
    assert result["sxk00c"] == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_lookup_prior_multipliers_empty_geohashes_short_circuits():
    fake_db = _FakeSession([])
    result = await traffic_prior_service.lookup_prior_multipliers(
        fake_db,
        region_code="IST",
        geohashes=set(),
        day_of_week=0,
        hour_bucket=0,
    )
    assert result == {}
    assert fake_db.execute_calls == 0
