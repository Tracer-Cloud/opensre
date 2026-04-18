from __future__ import annotations

import requests

from app.deployment.health import poll_deployment_health


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_poll_deployment_health_retries_until_success() -> None:
    calls: list[str] = []
    statuses = iter([503, 503, 200])

    def _http_get(url: str, *, timeout: float) -> _Resp:
        del timeout
        calls.append(url)
        return _Resp(next(statuses))

    sleep_calls: list[float] = []

    result = poll_deployment_health(
        "http://example.test",
        interval_seconds=1.5,
        max_attempts=5,
        request_timeout_seconds=1.0,
        http_get=_http_get,
        sleep=lambda seconds: sleep_calls.append(seconds),
    )

    assert result.status_code == 200
    assert result.attempts == 2
    assert result.url.endswith("/health")
    assert sleep_calls == [1.5]
    assert calls[:2] == ["http://example.test/health", "http://example.test/ok"]


def test_poll_deployment_health_times_out_for_unreachable_endpoint() -> None:
    def _http_get(url: str, *, timeout: float) -> _Resp:
        del url, timeout
        raise requests.exceptions.ConnectTimeout("connection timed out")

    sleep_calls: list[float] = []

    try:
        poll_deployment_health(
            "http://example.test",
            interval_seconds=0.5,
            max_attempts=3,
            request_timeout_seconds=0.1,
            http_get=_http_get,
            sleep=lambda seconds: sleep_calls.append(seconds),
        )
    except TimeoutError as exc:
        assert "timed out" in str(exc)
        assert "example.test" in str(exc)
    else:
        raise AssertionError("Expected TimeoutError")

    assert sleep_calls == [0.5, 0.5]


def test_poll_deployment_health_uses_explicit_health_url_without_fallback() -> None:
    calls: list[str] = []

    def _http_get(url: str, *, timeout: float) -> _Resp:
        del timeout
        calls.append(url)
        return _Resp(200)

    result = poll_deployment_health(
        "https://example.test/health",
        max_attempts=1,
        http_get=_http_get,
        sleep=lambda _seconds: None,
    )

    assert result.url == "https://example.test/health"
    assert calls == ["https://example.test/health"]
