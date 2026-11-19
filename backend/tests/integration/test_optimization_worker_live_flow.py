from __future__ import annotations

import uuid

import pytest
from redis import Redis
from sqlalchemy import select

from app.config import settings
from app.database import get_async_session_factory
from app.models.enums import OptimizationRunStatus, OptimizationRunType
from app.models.optimization_run import OptimizationRun
from app.workers import optimization_tasks
from tests.integration.live_api_helpers import auth_headers, signup_owner
from tests.integration.live_test_utils import run_async

pytestmark = pytest.mark.live_infra


def _get_me(client, *, access_token: str) -> dict:
    response = client.get(
        "/api/v1/auth/me",
        headers=auth_headers(access_token),
    )
    assert response.status_code == 200
    return response.json()


def _run_batch_task_inline(*, task_id: str, draft: bool, tenant_id: str) -> dict:
    optimization_tasks.run_batch_optimization.push_request(id=task_id, retries=0)
    try:
        return optimization_tasks.run_batch_optimization.run(
            draft=draft,
            tenant_id=tenant_id,
        )
    finally:
        optimization_tasks.run_batch_optimization.pop_request()


async def _get_run_snapshot(*, task_id: str) -> dict:
    async with get_async_session_factory()() as db:
        result = await db.execute(
            select(OptimizationRun).where(OptimizationRun.task_id == task_id)
        )
        run = result.scalar_one()
        return {
            "id": str(run.id),
            "tenant_id": str(run.tenant_id),
            "task_id": run.task_id,
            "run_type": run.run_type,
            "status": run.status,
            "routes_created": run.routes_created,
            "unassigned_count": run.unassigned_count,
            "solver_status": run.solver_status,
            "metrics": run.metrics,
            "error_message": run.error_message,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        }


def test_live_batch_worker_persists_pending_approval_run_metrics(monkeypatch, live_test_client):
    owner = signup_owner(live_test_client, "worker-success")
    me = _get_me(live_test_client, access_token=owner["access_token"])
    task_id = f"live-batch-{uuid.uuid4().hex[:10]}"
    captured: dict[str, object] = {}

    async def _fake_do_batch_optimization(
        run_id,
        *,
        draft,
        departure_time_iso,
        tenant_id,
        date_from_iso,
        date_to_iso,
    ):
        captured["run_id"] = run_id
        captured["draft"] = draft
        captured["departure_time_iso"] = departure_time_iso
        captured["tenant_id"] = tenant_id
        captured["date_from_iso"] = date_from_iso
        captured["date_to_iso"] = date_to_iso
        return {
            "routes_created": 1,
            "unassigned_count": 0,
            "unassigned_order_ids": [],
            "solver_status": "FEASIBLE",
            "optimization_run_id": run_id,
        }

    monkeypatch.setattr(
        optimization_tasks,
        "_do_batch_optimization",
        _fake_do_batch_optimization,
    )

    result = _run_batch_task_inline(
        task_id=task_id,
        draft=True,
        tenant_id=me["tenant_id"],
    )

    assert result["routes_created"] == 1
    assert result["unassigned_count"] == 0
    assert result["solver_status"] == "FEASIBLE"

    snapshot = run_async(_get_run_snapshot(task_id=task_id))
    assert snapshot["task_id"] == task_id
    assert snapshot["tenant_id"] == me["tenant_id"]
    assert snapshot["run_type"] == OptimizationRunType.BATCH
    assert snapshot["status"] == OptimizationRunStatus.PENDING_APPROVAL
    assert snapshot["routes_created"] == 1
    assert snapshot["unassigned_count"] == 0
    assert snapshot["solver_status"] == "FEASIBLE"
    assert snapshot["metrics"]["optimization_run_id"] == snapshot["id"]
    assert snapshot["error_message"] is None
    assert snapshot["finished_at"] is not None

    assert captured == {
        "run_id": snapshot["id"],
        "draft": True,
        "departure_time_iso": None,
        "tenant_id": me["tenant_id"],
        "date_from_iso": None,
        "date_to_iso": None,
    }


def test_live_batch_worker_records_locked_status_without_running_dispatch(monkeypatch, live_test_client):
    owner = signup_owner(live_test_client, "worker-locked")
    me = _get_me(live_test_client, access_token=owner["access_token"])
    task_id = f"live-batch-lock-{uuid.uuid4().hex[:10]}"
    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

    async def _unexpected_batch_call(*_args, **_kwargs):
        raise AssertionError("_do_batch_optimization should not run when the batch lock is already held")

    monkeypatch.setattr(
        optimization_tasks,
        "_do_batch_optimization",
        _unexpected_batch_call,
    )

    redis_client.set("optimization:batch:lock", "existing-owner", ex=60)
    try:
        result = _run_batch_task_inline(
            task_id=task_id,
            draft=False,
            tenant_id=me["tenant_id"],
        )
    finally:
        redis_client.delete("optimization:batch:lock")
        redis_client.close()

    assert result == {
        "routes_created": 0,
        "unassigned_count": 0,
        "solver_status": "LOCKED",
    }

    snapshot = run_async(_get_run_snapshot(task_id=task_id))
    assert snapshot["task_id"] == task_id
    assert snapshot["tenant_id"] == me["tenant_id"]
    assert snapshot["run_type"] == OptimizationRunType.BATCH
    assert snapshot["status"] == OptimizationRunStatus.LOCKED
    assert snapshot["routes_created"] == 0
    assert snapshot["unassigned_count"] == 0
    assert snapshot["solver_status"] == "LOCKED"
    assert snapshot["metrics"] == result
    assert snapshot["error_message"] is None
    assert snapshot["finished_at"] is not None
