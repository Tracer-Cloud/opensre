"""Credential-safe, size-capped attachment downloads for gateway transports.

Attachment URLs arrive inside inbound events, so a fetch that carries a bot
token must never send it to a host the vendor does not own — an
attacker-influenced URL would otherwise be handed the credential on the very
first request. The host is therefore allowlisted before anything is opened.

Redirects are then resolved here rather than by ``follow_redirects=True`` so the
allowlist is re-checked on every hop and the download stops before opening a
connection outside it. httpx does strip ``Authorization`` across origins on its
own, but that is a library detail to depend on rather than enforce, and it
carves out http-to-https redirects, which keep the header.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

# Hops we chase before giving up — enough for a vendor CDN handoff, not a loop.
_MAX_REDIRECTS = 5

_REDIRECT_STATUSES = frozenset(
    {
        HTTPStatus.MOVED_PERMANENTLY,
        HTTPStatus.FOUND,
        HTTPStatus.SEE_OTHER,
        HTTPStatus.TEMPORARY_REDIRECT,
        HTTPStatus.PERMANENT_REDIRECT,
    }
)


def is_allowed_host(url: str, host_suffixes: tuple[str, ...]) -> bool:
    """Whether ``url`` is an ``https`` URL on one of ``host_suffixes``.

    Matching is on the parsed hostname, so ``vendor.com.evil.example`` never
    passes as ``vendor.com``, and plain ``http`` is rejected outright.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in host_suffixes)


def _next_url(current_url: str, location: str, log_prefix: str) -> str | None:
    """Resolve a redirect ``location`` against ``current_url``; None if unusable."""
    if not location:
        logger.warning("%s redirect without a location header", log_prefix)
        return None
    try:
        target = urljoin(current_url, location)
        scheme = urlparse(target).scheme
    except ValueError:
        logger.warning("%s redirect target is invalid", log_prefix)
        return None
    if scheme != "https":
        logger.warning("%s redirect to a non-https target rejected", log_prefix)
        return None
    return target


def _read_capped(response: httpx.Response, max_bytes: int, log_prefix: str) -> bytes | None:
    """Drain a streamed response, dropping it entirely once it passes the cap."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            logger.info("%s payload exceeds %d bytes; skipped", log_prefix, max_bytes)
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def download_attachment(
    url: str,
    *,
    authorization: str,
    host_suffixes: tuple[str, ...],
    max_bytes: int,
    timeout: float,
    log_prefix: str,
    max_redirects: int = _MAX_REDIRECTS,
) -> bytes | None:
    """GET ``url`` with the credential pinned to ``host_suffixes``; None on failure.

    The first request is refused unless ``url`` is an https URL on an allowlisted
    host. Each redirect is followed manually only while its target remains on an
    allowlisted host. Oversized bodies are dropped rather than truncated.
    """
    if not (url and authorization):
        return None
    if not is_allowed_host(url, host_suffixes):
        logger.warning("%s attachment URL host rejected", log_prefix)
        return None
    current_url = url
    try:
        for _hop in range(max_redirects + 1):
            with httpx.stream(
                "GET",
                current_url,
                headers={"Authorization": authorization},
                follow_redirects=False,
                timeout=timeout,
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    target = _next_url(
                        current_url, response.headers.get("location", ""), log_prefix
                    )
                    if target is None:
                        return None
                    if not is_allowed_host(target, host_suffixes):
                        logger.warning("%s redirect target host rejected", log_prefix)
                        return None
                    current_url = target
                    continue
                if response.status_code != HTTPStatus.OK:
                    logger.warning("%s download HTTP %s", log_prefix, response.status_code)
                    return None
                return _read_capped(response, max_bytes, log_prefix)
    except httpx.HTTPError as exc:
        logger.warning("%s download failed: %s", log_prefix, type(exc).__name__)
        return None
    logger.warning("%s download exceeded %d redirects", log_prefix, max_redirects)
    return None


__all__ = ["download_attachment", "is_allowed_host"]
