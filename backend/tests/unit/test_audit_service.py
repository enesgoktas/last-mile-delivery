import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.audit_service import log_audit_event


@pytest.mark.asyncio
async def test_log_audit_event_persists_expected_fields():
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    actor_user_id = uuid.uuid4()

    event = await log_audit_event(
        db,
        action="auth.login",
        actor_user_id=actor_user_id,
        resource_type="user",
        resource_id=str(actor_user_id),
        details={"source": "api"},
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    db.add.assert_called_once_with(event)
    db.flush.assert_awaited_once()
    assert event.action == "auth.login"
    assert event.actor_user_id == actor_user_id
    assert event.resource_type == "user"
    assert event.resource_id == str(actor_user_id)
    assert event.details == {"source": "api"}
    assert event.ip_address == "127.0.0.1"
    assert event.user_agent == "pytest"
