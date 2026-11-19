from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import data_retention_service


class _FakeDB:
    def __init__(self, rowcounts: list[int]) -> None:
        self._rowcounts = list(rowcounts)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        rowcount = self._rowcounts.pop(0) if self._rowcounts else 0
        return SimpleNamespace(rowcount=rowcount)


def _table_name(statement) -> str | None:
    return getattr(getattr(statement, "table", None), "name", None)


@pytest.mark.asyncio
async def test_run_retention_cleanup_deletes_all_target_tables(monkeypatch):
    now = datetime(2026, 2, 24, 18, 0, 0)
    # Batched deletes: each model executes one DELETE per batch; all counts < BATCH_SIZE
    # so each model completes in one iteration → 4 execute calls total.
    db = _FakeDB([3, 5, 7, 11])

    monkeypatch.setattr(data_retention_service.settings, "LOCATION_RETENTION_DAYS", 90)
    monkeypatch.setattr(data_retention_service.settings, "WEBHOOK_LOG_RETENTION_DAYS", 90)
    monkeypatch.setattr(data_retention_service.settings, "NOTIFICATION_RETENTION_DAYS", 90)
    monkeypatch.setattr(data_retention_service.settings, "AUDIT_LOG_RETENTION_DAYS", 365)

    summary = await data_retention_service.run_retention_cleanup(db, now=now)

    assert summary["deleted_location_snapshots"] == 3
    assert summary["deleted_webhook_logs"] == 5
    assert summary["deleted_notifications"] == 7
    assert summary["deleted_audit_logs"] == 11
    assert len(db.statements) == 4

    # Batched deletes use DELETE WHERE id IN (SELECT id ... LIMIT n);
    # the outer WHERE column is 'id', not the timestamp column.
    tables = {_table_name(statement) for statement in db.statements}
    assert "location_snapshots" in tables
    assert "webhook_delivery_log" in tables
    assert "notifications" in tables
    assert "audit_logs" in tables


@pytest.mark.asyncio
async def test_run_retention_cleanup_skips_when_retention_disabled(monkeypatch):
    now = datetime(2026, 2, 24, 18, 0, 0)
    db = _FakeDB([])

    monkeypatch.setattr(data_retention_service.settings, "LOCATION_RETENTION_DAYS", 0)
    monkeypatch.setattr(data_retention_service.settings, "WEBHOOK_LOG_RETENTION_DAYS", 0)
    monkeypatch.setattr(data_retention_service.settings, "NOTIFICATION_RETENTION_DAYS", 0)
    monkeypatch.setattr(data_retention_service.settings, "AUDIT_LOG_RETENTION_DAYS", 0)

    summary = await data_retention_service.run_retention_cleanup(db, now=now)

    assert summary["deleted_location_snapshots"] == 0
    assert summary["deleted_webhook_logs"] == 0
    assert summary["deleted_notifications"] == 0
    assert summary["deleted_audit_logs"] == 0
    assert summary["cutoffs"]["location_recorded_before"] is None
    assert summary["cutoffs"]["webhook_sent_before"] is None
    assert summary["cutoffs"]["notification_created_before"] is None
    assert summary["cutoffs"]["audit_created_before"] is None
    assert db.statements == []
