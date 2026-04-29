"""Shared URL validation helpers."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse


def is_loopback_host(host: str) -> bool:
    """Return True when ``host`` identifies localhost or a loopback IP."""
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_https_or_loopback_http_url(
    value: str,
    *,
    service_name: str,
    field_name: str = "base_url",
) -> str:
    """Allow HTTPS URLs, plus plaintext HTTP only for loopback targets."""
    if not value:
        return ""

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme == "https" and parsed.netloc:
        return value
    if scheme == "http" and parsed.netloc and is_loopback_host(parsed.hostname or ""):
        return value
    raise ValueError(
        f"{service_name} {field_name} must use https:// unless targeting localhost/loopback."
    )
