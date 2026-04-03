from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from app.analytics import provider
from app.analytics.events import Event


def _clear_telemetry_env(monkeypatch) -> None:
    for name in provider._CI_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in (
        "CI",
        "DO_NOT_TRACK",
        "OPENSRE_ANALYTICS_DISABLED",
        "OPENSRE_NO_TELEMETRY",
        "OPENSRE_TELEMETRY_DEBUG",
        "PYTEST_CURRENT_TEST",
    ):
        monkeypatch.delenv(name, raising=False)


def _configure_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)


def test_base_properties_include_machine_architecture() -> None:
    properties = provider._base_properties()
    assert "machine_arch" in properties
    assert properties["machine_arch"] != ""


def test_generic_ci_env_does_not_disable_analytics(monkeypatch) -> None:
    _clear_telemetry_env(monkeypatch)
    monkeypatch.setenv("CI", "true")

    assert provider._is_ci_environment() is False
    assert provider._is_test_environment() is False
    assert provider._is_opted_out() is False


def test_hosted_ci_environment_disables_analytics(monkeypatch) -> None:
    _clear_telemetry_env(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert provider._is_ci_environment() is True
    assert provider._is_opted_out() is True


def test_pytest_environment_disables_analytics(monkeypatch) -> None:
    _clear_telemetry_env(monkeypatch)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "app/analytics/provider_test.py::test_case")

    assert provider._is_test_environment() is True
    assert provider._is_opted_out() is True


@pytest.mark.parametrize(
    "env_name",
    ["OPENSRE_NO_TELEMETRY", "OPENSRE_ANALYTICS_DISABLED", "DO_NOT_TRACK"],
)
def test_explicit_opt_out_envs_disable_analytics(monkeypatch, env_name: str) -> None:
    _clear_telemetry_env(monkeypatch)
    monkeypatch.setenv(env_name, "1")

    assert provider._is_opted_out() is True


def test_capture_first_run_if_needed_skips_files_when_disabled(monkeypatch, tmp_path: Path) -> None:
    _clear_telemetry_env(monkeypatch)
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    provider.capture_first_run_if_needed()

    assert provider._ANONYMOUS_ID_PATH.exists() is False
    assert provider._FIRST_RUN_PATH.exists() is False


def test_capture_posts_expected_payload(monkeypatch, tmp_path: Path) -> None:
    _clear_telemetry_env(monkeypatch)
    _configure_paths(monkeypatch, tmp_path)

    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

    def fake_post(url: str, *, json: dict[str, object], timeout: float) -> _Response:
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(provider.httpx, "post", fake_post)

    provider.capture(
        Event.COMMAND_COMPLETED,
        {
            "command": "health",
            "exit_code": 0,
            "success": True,
            "duration_ms": 12,
        },
    )

    assert captured["url"] == f"{provider._POSTHOG_HOST}/capture/"
    assert captured["timeout"] == provider._SEND_TIMEOUT

    payload = cast(dict[str, object], captured["json"])
    assert payload["api_key"] == provider._POSTHOG_API_KEY
    assert payload["event"] == Event.COMMAND_COMPLETED.value

    properties = cast(dict[str, object], payload["properties"])
    assert properties["command"] == "health"
    assert properties["exit_code"] == 0
    assert properties["success"] is True
    assert properties["duration_ms"] == 12
    assert properties["machine_arch"] != ""
    assert properties["distinct_id"] == provider._ANONYMOUS_ID_PATH.read_text(encoding="utf-8").strip()


def test_capture_reuses_anonymous_id(monkeypatch, tmp_path: Path) -> None:
    _clear_telemetry_env(monkeypatch)
    _configure_paths(monkeypatch, tmp_path)

    distinct_ids: list[str] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

    def fake_post(_url: str, *, json: dict[str, object], timeout: float) -> _Response:
        assert timeout == provider._SEND_TIMEOUT
        properties = cast(dict[str, object], json["properties"])
        distinct_ids.append(str(properties["distinct_id"]))
        return _Response()

    monkeypatch.setattr(provider.httpx, "post", fake_post)

    provider.capture(Event.COMMAND_COMPLETED, {"command": "health", "exit_code": 0, "success": True, "duration_ms": 1})
    provider.capture(Event.COMMAND_COMPLETED, {"command": "health", "exit_code": 0, "success": True, "duration_ms": 2})

    assert len(distinct_ids) == 2
    assert distinct_ids[0] == distinct_ids[1]


def test_debug_mode_logs_payload_without_network_send(monkeypatch, tmp_path: Path, capsys) -> None:
    _clear_telemetry_env(monkeypatch)
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENSRE_TELEMETRY_DEBUG", "1")

    def fail_post(*_args, **_kwargs):
        raise AssertionError("debug mode should not send telemetry")

    monkeypatch.setattr(provider.httpx, "post", fail_post)

    provider.capture(
        Event.COMMAND_COMPLETED,
        {
            "access_token": "super-secret-token",
            "command": "health",
            "duration_ms": 12,
            "exit_code": 0,
            "success": True,
        },
    )
    output = capsys.readouterr().err

    assert "[telemetry]" in output
    assert Event.COMMAND_COMPLETED.value in output
    assert provider._POSTHOG_API_KEY not in output
    assert "api_key" not in output
    assert "super-secret-token" not in output
    assert '"access_token": "[REDACTED]"' in output


def test_debug_mode_redacts_sensitive_key_substrings(monkeypatch, tmp_path: Path, capsys) -> None:
    _clear_telemetry_env(monkeypatch)
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENSRE_TELEMETRY_DEBUG", "1")
    def fail_post(*_args, **_kwargs):
        raise AssertionError("debug mode should not send telemetry")

    monkeypatch.setattr(provider.httpx, "post", fail_post)

    provider.capture(
        Event.COMMAND_COMPLETED,
        {
            "command": "health",
            "duration_ms": 1,
            "exit_code": 0,
            "oauth_refresh_token": "do-not-leak",
            "success": True,
        },
    )
    output = capsys.readouterr().err

    assert "do-not-leak" not in output
    assert '"oauth_refresh_token": "[REDACTED]"' in output
