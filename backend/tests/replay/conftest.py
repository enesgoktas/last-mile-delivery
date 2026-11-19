"""Replay test infrastructure.

Replay tests re-run the solver on recorded production snapshots and assert:
- served_count delta ≤ 2% vs baseline recording
- generalized_cost delta ≤ 5% vs baseline recording
- no URGENT request goes from served (in baseline) to unserved

Snapshot format
---------------
Each snapshot is a JSON file at tests/replay/snapshots/<date>_<id>.json with:
{
    "vrp_input": { ... VRPInput-serialisable dict ... },
    "baseline": {
        "served_count": int,
        "unserved_urgent": [],
        "total_generalized_cost": int
    }
}

Populate snapshots by running:
    python -m tests.replay.record --date 2026-05-06

That script (not yet written — requires DB access) reads production orders +
couriers for the given date, calls the solver, and writes a snapshot file.
No snapshot files are committed — they live outside the repo (internal data).
The REPLAY_SNAPSHOT_DIR env var points the loader at the right directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

SNAPSHOT_DIR = Path(os.environ.get("REPLAY_SNAPSHOT_DIR", Path(__file__).parent / "snapshots"))


def _load_snapshots() -> list[dict]:
    if not SNAPSHOT_DIR.exists():
        return []
    return [
        json.loads(p.read_text())
        for p in sorted(SNAPSHOT_DIR.glob("*.json"))
    ]


def pytest_collect_file(parent, file_path):
    """No-op hook — snapshots are loaded by fixture, not as pytest files."""


@pytest.fixture(params=_load_snapshots(), ids=lambda s: s.get("id", "snapshot"))
def replay_snapshot(request):
    """Parametrized fixture that yields one snapshot dict per JSON file found."""
    return request.param
