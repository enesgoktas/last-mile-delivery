import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.enums import ImportJobStatus, OrderStatus
from app.models.import_job import ImportJob
from app.services import import_service
from app.utils.compat import UTC


def test_render_invalid_rows_csv_includes_error_rows():
    job = ImportJob(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        created_by_user_id=uuid.uuid4(),
        status=ImportJobStatus.PREVIEW_READY,
        total_rows=2,
        valid_rows=1,
        invalid_rows=1,
        payload=[],
        invalid_details=[
            {
                "row_number": 3,
                "errors": ["Pickup address could not be geocoded"],
                "raw": {
                    "external_ref": "ref-1",
                    "pickup_address": "Unknown",
                    "delivery_address": "Known",
                    "weight_kg": "2.1",
                    "earliest_delivery": "2026-02-24T10:00:00",
                    "latest_delivery": "2026-02-24T11:00:00",
                    "customer_email": "a@b.com",
                    "customer_phone": "555123",
                    "notes": "",
                },
            }
        ],
    )

    csv_content = import_service.render_invalid_rows_csv(job)

    assert "row_number,errors,external_ref" in csv_content
    assert "Pickup address could not be geocoded" in csv_content
    assert "ref-1" in csv_content


class _ExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, execute_values=None):
        self.execute_values = list(execute_values or [])
        self.added = []
        self.flush_count = 0
        self.refresh_count = 0

    async def execute(self, _query):
        if not self.execute_values:
            return _ExecuteResult(None)
        return _ExecuteResult(self.execute_values.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flush_count += 1

    async def refresh(self, _obj):
        self.refresh_count += 1


@pytest.mark.asyncio
async def test_preview_csv_import_geocodes_unique_addresses_once(monkeypatch):
    calls: list[str] = []
    address_map = {
        "Warehouse A": (41.0, 29.0),
        "Customer X": (41.01, 29.01),
    }

    async def _mock_geocode(address: str):
        calls.append(address)
        return address_map.get(address)

    monkeypatch.setattr(import_service, "geocode_address", _mock_geocode)

    csv_content = "\n".join(
        [
            "external_ref,pickup_address,delivery_address,weight_kg,is_fragile,earliest_delivery,latest_delivery,customer_email,customer_phone,notes",
            "ext-1,Warehouse A,Customer X,2.5,false,2026-02-24T10:00:00,2026-02-24T11:00:00,a@example.com,+15550001111,",
            "ext-2,Warehouse A,Customer X,3.1,true,2026-02-24T12:00:00,2026-02-24T13:00:00,b@example.com,+15550002222,fragile",
        ]
    )

    db = _FakeDB()
    job = await import_service.preview_csv_import(
        db,
        actor_user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        csv_content=csv_content,
    )

    assert job.total_rows == 2
    assert job.valid_rows == 2
    assert job.invalid_rows == 0
    assert sorted(calls) == ["Customer X", "Warehouse A"]
    assert db.flush_count == 1
    assert db.refresh_count == 1


@pytest.mark.asyncio
async def test_publish_import_job_transitions_created_drafts_to_pending(monkeypatch):
    actor_user_id = uuid.uuid4()
    import_id = uuid.uuid4()
    job = SimpleNamespace(
        id=import_id,
        tenant_id=uuid.uuid4(),
        created_by_user_id=actor_user_id,
        status=ImportJobStatus.PREVIEW_READY,
        payload=[
            {
                "type": "BULK",
                "priority": "NORMAL",
                "pickup_address": "Warehouse A",
                "pickup_lat": 41.0,
                "pickup_lon": 29.0,
                "delivery_address": "Customer X",
                "delivery_lat": 41.01,
                "delivery_lon": 29.01,
                "weight_kg": 2.0,
                "is_fragile": False,
                "is_temp_sensitive": False,
                "earliest_pickup": None,
                "latest_pickup": None,
                "earliest_delivery": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "latest_delivery": (datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)).isoformat(),
                "customer_email": "a@example.com",
                "customer_phone": None,
                "external_ref": "ext-1",
                "notes": None,
            }
        ],
        published_at=None,
    )
    db = _FakeDB(execute_values=[job])
    transitions: list[OrderStatus] = []

    async def _mock_create_order(*_args, **_kwargs):
        return SimpleNamespace(id=uuid.uuid4(), status=OrderStatus.DRAFT)

    async def _mock_transition(_db, order, target, **_kwargs):
        transitions.append(target)
        order.status = target

    monkeypatch.setattr(import_service.order_service, "create_order", _mock_create_order)
    monkeypatch.setattr(import_service.order_service, "apply_order_status_transition", _mock_transition)

    published_job, created_ids = await import_service.publish_import_job(
        db,
        import_id=import_id,
        actor_user_id=actor_user_id,
        tenant_id=job.tenant_id,
    )

    assert published_job.status == ImportJobStatus.PUBLISHED
    assert published_job.published_at is not None
    assert len(created_ids) == 1
    assert transitions == [OrderStatus.PENDING]
