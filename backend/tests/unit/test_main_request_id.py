from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.utils.request_context import CORRELATION_ID_HEADER, REQUEST_ID_HEADER


def test_request_id_header_is_generated(monkeypatch):
    monkeypatch.setattr(settings, "SKIP_DB_STARTUP_CHECKS", True)
    monkeypatch.setattr(settings, "JWT_SECRET", "test-secret-1234")

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    request_id = response.headers.get(REQUEST_ID_HEADER)
    correlation_id = response.headers.get(CORRELATION_ID_HEADER)
    assert request_id
    assert correlation_id == request_id


def test_request_id_header_is_propagated(monkeypatch):
    monkeypatch.setattr(settings, "SKIP_DB_STARTUP_CHECKS", True)
    monkeypatch.setattr(settings, "JWT_SECRET", "test-secret-1234")
    expected_request_id = "test-request-id-123"

    with TestClient(app) as client:
        response = client.get("/health", headers={REQUEST_ID_HEADER: expected_request_id})

    assert response.status_code == 200
    assert response.headers.get(REQUEST_ID_HEADER) == expected_request_id
    assert response.headers.get(CORRELATION_ID_HEADER) == expected_request_id


def test_correlation_id_header_is_propagated(monkeypatch):
    monkeypatch.setattr(settings, "SKIP_DB_STARTUP_CHECKS", True)
    monkeypatch.setattr(settings, "JWT_SECRET", "test-secret-1234")

    with TestClient(app) as client:
        response = client.get(
            "/health",
            headers={CORRELATION_ID_HEADER: "external-correlation-id-123"},
        )

    assert response.status_code == 200
    assert response.headers.get(CORRELATION_ID_HEADER) == "external-correlation-id-123"
