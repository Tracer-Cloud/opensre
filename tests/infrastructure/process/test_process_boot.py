"""Characterization: shared process boot order for every host profile.

``boot_process`` owns the load-bearing sequence so CLI, gateway, and web do not
invent parallel boot stories. Order is pinned here — if a host needs a different
step, change the profile or keep the step surface-owned (CLI Sentry / Rich
adapters), do not reorder the shared path casually.
"""

from __future__ import annotations

import builtins
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from infrastructure.observability.trace.observations import (
    get_observation_sink,
    is_observation_sink_active,
    set_observation_sink,
)
from infrastructure.process.boot import (
    CLI_PROFILE,
    EMBEDDED_PROFILE,
    GATEWAY_PROFILE,
    SCHEDULED_COMMAND_PROFILE,
    SCHEDULER_WORKER_PROFILE,
    WEB_PROFILE,
    BootStep,
    CapabilityRegistrars,
    boot_process,
    reset_process_runtime_for_tests,
)
from tests.utils.observations import RecordingObservationSink

_ENV = "infrastructure.process.boot.bootstrap_opensre_env_once"


@pytest.fixture(autouse=True)
def _reset_process_runtime() -> Iterator[None]:
    """``boot_process`` runs a profile once per process; each test boots afresh."""
    reset_process_runtime_for_tests()
    yield
    reset_process_runtime_for_tests()


def _registrars(record: Callable[[str], None] = lambda _name: None) -> CapabilityRegistrars:
    """Registrars that record the harness step as ``adapters`` and delivery as ``runners``."""
    return CapabilityRegistrars(
        integration_adapters=lambda: record("adapters"),
        tool_adapters=lambda: None,
        cli_auth_checker=lambda: None,
        scheduled_delivery=lambda: record("runners"),
    )


@pytest.mark.parametrize("custom_sink", [False, True], ids=["default", "custom"])
def test_boot_preserves_observation_sink_with_legacy_credentials(
    monkeypatch: pytest.MonkeyPatch, custom_sink: bool
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("OPENSRE_LANGFUSE_DISABLED", "0")
    monkeypatch.setattr(_ENV, lambda **_kw: None)
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry", lambda **_kw: None
    )
    monkeypatch.setattr(
        "infrastructure.safety.sandbox.capabilities.boot_capability_warnings", lambda: []
    )
    monkeypatch.setattr("core.llm.internal.preload.preload_llm_clients", lambda: None)
    set_observation_sink(RecordingObservationSink() if custom_sink else None)
    original_sink = get_observation_sink()
    sdk_imports: list[str] = []
    original_import = builtins.__import__

    def _guard_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "langfuse" or name.startswith("langfuse."):
            sdk_imports.append(name)
            raise AssertionError("Process boot must not import the removed tracing SDK")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guard_import)
    for profile in (
        CLI_PROFILE,
        GATEWAY_PROFILE,
        WEB_PROFILE,
        SCHEDULER_WORKER_PROFILE,
        SCHEDULED_COMMAND_PROFILE,
        EMBEDDED_PROFILE,
    ):
        boot_process(profile, _registrars())
        assert get_observation_sink() is original_sink
        assert is_observation_sink_active() is custom_sink
    assert sdk_imports == []


def test_boot_process_gateway_order(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(_ENV, lambda **_kw: order.append("env"))
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: order.append("sentry"),
    )
    monkeypatch.setattr(
        "infrastructure.safety.sandbox.capabilities.boot_capability_warnings",
        lambda: order.append("caps") or ["curl missing"],
    )
    monkeypatch.setattr(
        "core.llm.internal.preload.preload_llm_clients",
        lambda: order.append("preload"),
    )

    boot_process(GATEWAY_PROFILE, _registrars(order.append), logger=logging.getLogger("t"))

    assert order == ["env", "sentry", "adapters", "caps", "preload"], order


def test_boot_process_cli_boots_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI_PROFILE leaves Sentry and Rich adapters to surfaces/cli/startup."""
    order: list[str] = []
    monkeypatch.setattr(_ENV, lambda **_kw: order.append("env"))
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: order.append("sentry"),
    )

    boot_process(CLI_PROFILE, _registrars(order.append))

    assert order == ["env"], order


def test_boot_process_web_skips_llm_preload(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(_ENV, lambda **_kw: order.append("env"))
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: order.append("sentry"),
    )
    monkeypatch.setattr(
        "core.llm.internal.preload.preload_llm_clients",
        lambda: order.append("preload"),
    )

    boot_process(WEB_PROFILE, _registrars(order.append))

    assert order == ["env", "sentry", "adapters"], order


def test_boot_process_is_idempotent_per_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def _env(**_kw: Any) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(_ENV, _env)
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: None,
    )

    boot_process(WEB_PROFILE, _registrars())
    boot_process(WEB_PROFILE, _registrars())
    assert calls == 1


class TestEmbeddedProfile:
    """The recipe ``main.py`` documents for driving the agent from Python."""

    def test_registers_harness_adapters_so_tools_resolve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange: without adapter registration the harness starts but no tool
        # resolves, which is the failure an embedder hits with env-only boot.
        installed: list[str] = []
        monkeypatch.setattr(_ENV, lambda **_kw: None)

        # Act
        boot_process(EMBEDDED_PROFILE, _registrars(installed.append))

        # Assert: adapters yes, scheduler runners no — an embedded host is not
        # running the scheduler.
        assert installed == ["adapters"]

    def test_leaves_error_reporting_to_the_host(self) -> None:
        # Arrange / Act: a library embedded in someone else's process must not
        # install its own Sentry client or preload LLM clients on import.

        # Assert
        assert BootStep.SENTRY not in EMBEDDED_PROFILE.steps
        assert BootStep.PRELOAD_LLM not in EMBEDDED_PROFILE.steps
        assert BootStep.SCHEDULER_RUNNERS not in EMBEDDED_PROFILE.steps


def test_gateway_reports_missing_capabilities_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a long-running daemon is the host least able to notice a missing
    # binary later, so its boot warnings are the ones worth pinning. They were
    # dropped once already while converting flags to a step set.
    warned: list[str] = []
    monkeypatch.setattr(_ENV, lambda **_kw: None)
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry", lambda **_kw: None
    )
    monkeypatch.setattr("core.llm.internal.preload.preload_llm_clients", lambda: None)
    monkeypatch.setattr(
        "infrastructure.safety.sandbox.capabilities.boot_capability_warnings",
        lambda: ["kubectl missing"],
    )

    class _Log:
        def warning(self, _fmt: str, *args: object) -> None:
            warned.append(" ".join(str(a) for a in args))

    # Act
    boot_process(GATEWAY_PROFILE, _registrars(), logger=_Log())  # type: ignore[arg-type]

    # Assert
    assert warned == ["gateway kubectl missing"]


def test_scheduler_worker_profile_boots_runners_with_scheduler_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``opensre cron start`` uses SCHEDULER_WORKER_PROFILE (dedicated daemon)."""
    ran: list[str] = []
    monkeypatch.setattr(_ENV, lambda **_kw: ran.append("env"))
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: ran.append(f"sentry:{_kw.get('entrypoint')}"),
    )

    boot_process(SCHEDULER_WORKER_PROFILE, _registrars(ran.append))

    assert ran == ["env", "sentry:scheduler", "adapters", "runners"]


def test_scheduled_command_profile_dispatches_without_touching_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: cron / digest / report commands run inside an already-booted CLI
    # process. They need the runners that dispatch scheduled work, but Sentry is
    # the CLI's to own (its update path tolerates a missing SDK).
    ran: list[str] = []
    monkeypatch.setattr(_ENV, lambda **_kw: ran.append("env"))
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: ran.append("sentry"),
    )

    # Act
    boot_process(SCHEDULED_COMMAND_PROFILE, _registrars(ran.append))

    # Assert
    assert ran == ["env", "adapters", "runners"]
