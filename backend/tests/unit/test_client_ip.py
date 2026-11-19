from starlette.requests import Request

from app.config import settings
from app.utils.client_ip import get_request_client_ip


def _build_request(*, client_host: str | None, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in (headers or {}).items()
        ],
    }
    if client_host is not None:
        scope["client"] = (client_host, 12345)
    return Request(scope)


def test_get_request_client_ip_uses_direct_client_by_default(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")

    request = _build_request(client_host="203.0.113.10")

    assert get_request_client_ip(request) == "203.0.113.10"


def test_get_request_client_ip_uses_cf_connecting_ip_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")

    request = _build_request(
        client_host="10.1.2.3",
        headers={"CF-Connecting-IP": "198.51.100.24"},
    )

    assert get_request_client_ip(request) == "198.51.100.24"


def test_get_request_client_ip_uses_first_forwarded_for_hop_from_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")

    request = _build_request(
        client_host="10.1.2.3",
        headers={"X-Forwarded-For": "198.51.100.24, 10.1.2.3"},
    )

    assert get_request_client_ip(request) == "198.51.100.24"


def test_get_request_client_ip_ignores_proxy_headers_from_untrusted_source(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")

    request = _build_request(
        client_host="203.0.113.10",
        headers={"X-Forwarded-For": "198.51.100.24"},
    )

    assert get_request_client_ip(request) == "203.0.113.10"


def test_get_request_client_ip_falls_back_when_forwarded_header_is_invalid(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")

    request = _build_request(
        client_host="10.1.2.3",
        headers={"X-Forwarded-For": "not-an-ip, 10.1.2.3"},
    )

    assert get_request_client_ip(request) == "10.1.2.3"


def test_get_request_client_ip_returns_default_when_client_missing(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "")

    request = _build_request(client_host=None)

    assert get_request_client_ip(request, default="unknown") == "unknown"
