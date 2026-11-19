from __future__ import annotations

import pytest

from app.workers import retention_tasks


class _FakeDB:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


class _SessionCtx:
    def __init__(self, db: _FakeDB):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_cleanup_worker_commits_and_returns_summary(monkeypatch):
    db = _FakeDB()
    expected = {"deleted_location_snapshots": 2}

    async def _mock_cleanup(_db):
        return expected

    monkeypatch.setattr(retention_tasks, "celery_async_session", lambda: _SessionCtx(db))
    monkeypatch.setattr(
        retention_tasks.data_retention_service,
        "run_retention_cleanup",
        _mock_cleanup,
    )

    result = await retention_tasks._cleanup()

    assert result == expected
    assert db.committed is True


def test_run_data_retention_cleanup_task_delegates_to_run_async(monkeypatch):
    expected = {"deleted_location_snapshots": 1}

    def _mock_run_async(coro):
        coro.close()
        return expected

    monkeypatch.setattr(retention_tasks, "_run_async", _mock_run_async)

    result = retention_tasks.run_data_retention_cleanup()

    assert result == expected
