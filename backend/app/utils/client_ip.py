import ipaddress

from fastapi import Request

from app.config import settings


def _normalize_ip(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _parse_forwarded_for(value: str | None) -> str | None:
    if not value:
        return None
    first_hop = value.split(",")[0].strip()
    return _normalize_ip(first_hop)


def _is_trusted_proxy(host: str | None) -> bool:
    if not settings.TRUST_PROXY_HEADERS or not host:
        return False

    normalized_host = _normalize_ip(host)
    if normalized_host is None:
        return False

    proxy_ip = ipaddress.ip_address(normalized_host)
    for cidr in settings.trusted_proxy_cidrs:
        try:
            if proxy_ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def get_request_client_ip(request: Request, *, default: str | None = None) -> str | None:
    direct_host = request.client.host if request.client and request.client.host else default
    if not _is_trusted_proxy(direct_host):
        return direct_host

    for header_name, extractor in (
        ("cf-connecting-ip", _normalize_ip),
        ("x-forwarded-for", _parse_forwarded_for),
        ("x-real-ip", _normalize_ip),
    ):
        resolved = extractor(request.headers.get(header_name))
        if resolved is not None:
            return resolved

    return direct_host
