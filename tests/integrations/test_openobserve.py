from __future__ import annotations

import pytest

from config.constants.openobserve import OPENOBSERVE_TRACE_STREAM_ENV
from integrations.openobserve import classify
from integrations.openobserve.verifier import verify_openobserve


def test_classify_preserves_trace_stream() -> None:
    cfg, service = classify(
        {
            "base_url": "https://openobserve.example.com",
            "api_token": "token",
            "stream": "logs",
            "trace_stream": "  traces_prod  ",
        },
        "oo-1",
    )

    assert service == "openobserve"
    assert cfg is not None
    assert cfg.stream == "logs"
    assert cfg.trace_stream == "traces_prod"
    assert cfg.integration_id == "oo-1"


def test_load_env_integrations_includes_trace_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations._catalog_impl import load_env_integrations

    monkeypatch.setenv("OPENOBSERVE_URL", "https://openobserve.example.com")
    monkeypatch.setenv("OPENOBSERVE_TOKEN", "token")
    monkeypatch.setenv("OPENOBSERVE_STREAM", "logs")
    monkeypatch.setenv(OPENOBSERVE_TRACE_STREAM_ENV, "traces_prod")

    records = [item for item in load_env_integrations() if item["service"] == "openobserve"]

    assert len(records) == 1
    credentials = records[0]["credentials"]
    assert credentials["stream"] == "logs"
    assert credentials["trace_stream"] == "traces_prod"


def test_verify_openobserve_missing_base_url() -> None:
    result = verify_openobserve("local env", {"api_token": "token"})

    assert result["status"] == "missing"
    assert "base_url" in result["detail"]


def test_verify_openobserve_missing_auth() -> None:
    result = verify_openobserve("local env", {"base_url": "https://openobserve.example.com"})

    assert result["status"] == "missing"
    assert "api token" in result["detail"].lower()


def test_verify_openobserve_passes_with_api_token() -> None:
    result = verify_openobserve(
        "local env",
        {"base_url": "https://openobserve.example.com/", "api_token": "token"},
    )

    assert result["status"] == "passed"
    assert result["detail"].endswith("at https://openobserve.example.com.")


def test_verify_openobserve_passes_with_username_password() -> None:
    result = verify_openobserve(
        "local env",
        {
            "base_url": "https://openobserve.example.com",
            "username": "user",
            "password": "pass",
        },
    )

    assert result["status"] == "passed"
    assert result["detail"].endswith("at https://openobserve.example.com.")
