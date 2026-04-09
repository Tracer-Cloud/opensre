"""Shared deployment health polling helpers."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class HealthPollStatus:
    """Result for a successful health poll."""

    url: str
    attempts: int
    status_code: int
    elapsed_seconds: float


def _build_health_urls(base_url: str) -> tuple[str, ...]:
    """Return health URL candidates for a deployment base URL."""
    stripped = base_url.strip().rstrip("/")
    if stripped.endswith("/health") or stripped.endswith("/ok"):
        return (stripped,)
    return (f"{stripped}/health", f"{stripped}/ok")


def poll_deployment_health(
    base_url: str,
    *,
    interval_seconds: float = 5.0,
    max_attempts: int = 60,
    request_timeout_seconds: float = 5.0,
    http_get: Callable[..., object] = requests.get,
    sleep: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.monotonic,
) -> HealthPollStatus:
    """Poll deployment health with ``/health`` then ``/ok`` fallback.

    Raises:
        TimeoutError: When no candidate endpoint returns HTTP 200 in time.
    """
    urls = _build_health_urls(base_url)
    started = time_fn()
    last_status: int | None = None
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        for url in urls:
            try:
                response = http_get(url, timeout=request_timeout_seconds)
                status_code = int(getattr(response, "status_code", 0))
                if status_code == 200:
                    return HealthPollStatus(
                        url=url,
                        attempts=attempt,
                        status_code=status_code,
                        elapsed_seconds=time_fn() - started,
                    )
                last_status = status_code
            except requests.exceptions.RequestException as exc:
                last_error = str(exc)

        if attempt < max_attempts:
            sleep(max(interval_seconds, 0.0))

    detail = (
        f"last status={last_status}"
        if last_status is not None
        else f"last error={last_error or 'none'}"
    )
    elapsed = time_fn() - started
    raise TimeoutError(
        f"Deployment health check timed out after {elapsed:.1f}s "
        f"({max_attempts} attempts, candidates={list(urls)}, {detail})"
    )


__all__ = ["HealthPollStatus", "poll_deployment_health"]
