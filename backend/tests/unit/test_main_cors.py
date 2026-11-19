import re

from app.config import settings
from app.main import resolve_cors_middleware_settings


def test_resolve_cors_settings_uses_explicit_values(monkeypatch):
    monkeypatch.setattr(
        settings,
        "CORS_ALLOWED_ORIGINS",
        "https://dispatch.example.com, https://ops.example.com",
    )
    monkeypatch.setattr(settings, "CORS_ALLOWED_METHODS", "GET,POST")
    monkeypatch.setattr(settings, "CORS_ALLOWED_HEADERS", "Authorization,Content-Type")
    monkeypatch.setattr(settings, "CORS_ALLOW_CREDENTIALS", True)

    origins, origin_regex, allow_credentials, methods, headers = resolve_cors_middleware_settings()

    assert origins == ["https://dispatch.example.com", "https://ops.example.com"]
    assert origin_regex is not None
    assert allow_credentials is True
    assert methods == ["GET", "POST"]
    assert headers == ["Authorization", "Content-Type"]


def test_resolve_cors_settings_disables_credentials_for_wildcard_origin(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ALLOWED_ORIGINS", "*")
    monkeypatch.setattr(settings, "CORS_ALLOW_CREDENTIALS", True)

    origins, _, allow_credentials, _, _ = resolve_cors_middleware_settings()

    assert origins == ["*"]
    assert allow_credentials is False


def test_resolve_cors_settings_falls_back_to_safe_defaults(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ALLOWED_ORIGINS", "")
    monkeypatch.setattr(settings, "CORS_ALLOWED_METHODS", "")
    monkeypatch.setattr(settings, "CORS_ALLOWED_HEADERS", "")
    monkeypatch.setattr(settings, "CORS_ALLOW_CREDENTIALS", True)

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    origins, origin_regex, allow_credentials, methods, headers = resolve_cors_middleware_settings()

    assert origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert origin_regex is not None
    assert re.match(origin_regex, "http://192.168.1.108:5173")
    assert re.match(origin_regex, "http://10.0.0.42:5174")
    assert allow_credentials is True
    assert methods == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    assert headers == ["Authorization", "Content-Type", "X-API-Key"]


def test_resolve_cors_settings_disables_dev_lan_regex_in_production(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ALLOWED_ORIGINS", "")
    monkeypatch.setattr(settings, "CORS_ALLOWED_METHODS", "")
    monkeypatch.setattr(settings, "CORS_ALLOWED_HEADERS", "")
    monkeypatch.setattr(settings, "CORS_ALLOWED_ORIGIN_REGEX", "")
    monkeypatch.setattr(settings, "CORS_ALLOW_CREDENTIALS", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    origins, origin_regex, allow_credentials, methods, headers = resolve_cors_middleware_settings()

    assert origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert origin_regex is None
    assert allow_credentials is True
    assert methods == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    assert headers == ["Authorization", "Content-Type", "X-API-Key"]
