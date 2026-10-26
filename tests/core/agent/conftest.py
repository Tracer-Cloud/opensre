"""Pytest fixtures for co-located turn tests."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.grafana_cloud import load_env
from config.llm_auth.credentials import status as credential_status
from config.llm_auth.provider_catalog import provider_spec
from config.llm_settings import (
    get_configured_llm_provider,
    get_llm_provider_api_key_env,
    resolve_llm_settings,
)
from tests.core.agent._ci_gates import (
    running_in_github_actions,
)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path(__file__).resolve().parents[3]


_PROJECT_ROOT = _repo_root()
_ENV_PATH = _PROJECT_ROOT / ".env"
_TURN_TEST_DEFAULT_ENV = {
    "OPENSRE_SENTRY_DISABLED": "1",
    "OPENSRE_NO_TELEMETRY": "1",
}


def _skip_or_fail_live_llm(message: str) -> None:
    if running_in_github_actions():
        pytest.fail(message)
    pytest.skip(message)


def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    """Load project settings for co-located turn tests."""
    load_env(_ENV_PATH, override=False)


@pytest.fixture(autouse=True)
def _turn_test_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirror test-suite defaults while keeping env mutations isolated per test."""
    for key, value in _TURN_TEST_DEFAULT_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _disable_system_keyring(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep tests isolated from any real developer keychain entries."""
    if request.node.get_closest_marker("live_llm") is not None:
        return
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")


@pytest.fixture(autouse=True)
def _resolve_live_llm_configuration(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Let live LLM turn tests run with Anthropic or OpenAI credentials."""
    if request.node.get_closest_marker("live_llm") is None:
        yield
        return

    try:
        settings = resolve_llm_settings()
    except ValidationError as exc:
        provider = get_configured_llm_provider()
        env_var = get_llm_provider_api_key_env(provider)
        msg = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
        hint = f" configured provider={provider!r}"
        if env_var is not None:
            hint += f", required key={env_var}"
        _skip_or_fail_live_llm(
            f"Live LLM turn tests require usable LLM configuration:{hint}. {msg}"
        )

    auth = credential_status(settings.provider)
    if not auth.configured or auth.stale:
        _skip_or_fail_live_llm(
            "Live LLM turn tests require usable LLM credentials:"
            f" configured provider={settings.provider!r}, auth={auth.source}, detail={auth.detail}"
        )

    spec = provider_spec(settings.provider)
    if spec is not None and spec.credential_kind == "api_key" and spec.api_key_env:
        from config.llm_credentials import resolve_env_credential

        if not resolve_env_credential(spec.api_key_env):
            _skip_or_fail_live_llm(
                "Live LLM turn tests require a resolvable API key:"
                f" provider={settings.provider!r}, env={spec.api_key_env}"
            )

    from core.llm.factory import LLMRole, get_llm, reset_llm_clients

    monkeypatch.setenv("LLM_PROVIDER", settings.provider)
    reset_llm_clients()
    # credential_status can look fine while the provider SDK still refuses to
    # construct a client (empty/placeholder key, wrong env for the active
    # provider). Probe once here so live tests skip/fail at setup, not mid-call.
    try:
        get_llm(LLMRole.AGENT)
    except Exception as exc:
        detail = str(exc).lower()
        if any(
            marker in detail
            for marker in (
                "missing credentials",
                "invalid_api_key",
                "incorrect api key",
                "authenticationerror",
                "could not resolve credentials",
            )
        ):
            _skip_or_fail_live_llm(
                "Live LLM turn tests require a constructible provider client:"
                f" provider={settings.provider!r}. {exc}"
            )
        raise
    yield
    reset_llm_clients()


@pytest.fixture(autouse=True)
def _repl_execution_policy_auto_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elevated REPL actions prompt for confirmation; stdin is non-TTY under pytest."""
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.execution_confirm.DEFAULT_CONFIRM_FN",
        lambda _prompt: "y",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


_LIVE_LLM_SKIPS_IN_CI: list[str] = []


def _is_xdist_worker() -> bool:
    """True on pytest-xdist worker processes (not the controller)."""
    return os.getenv("PYTEST_XDIST_WORKER") is not None


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Fail the run if any live_llm test skips in CI (controller-only under xdist)."""
    if _is_xdist_worker() or not running_in_github_actions():
        return
    if report.when != "call" or not report.skipped:
        return
    if "live_llm" not in report.keywords:
        return
    _LIVE_LLM_SKIPS_IN_CI.append(f"{report.nodeid}: {report.longrepr}")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _is_xdist_worker() or not _LIVE_LLM_SKIPS_IN_CI:
        return
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_line("live_llm tests must not skip in CI (fix credentials or shard config):")
        for line in _LIVE_LLM_SKIPS_IN_CI:
            terminal.write_line(f"  - {line}")
    session.exitstatus = 1
