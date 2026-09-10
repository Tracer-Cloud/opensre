"""Characterization: shared process boot order for every host profile.

``configure_process`` owns the load-bearing sequence so CLI, gateway, and web
do not invent parallel boot stories. Order is pinned here — if a host needs a
different step, change the profile or keep the step surface-owned (CLI Sentry /
Rich adapters), do not reorder the shared path casually.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from bootstrap.process import (
    CLI_PROFILE,
    GATEWAY_PROFILE,
    WEB_PROFILE,
    configure_process,
)


def test_configure_process_gateway_order(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        "bootstrap.process.bootstrap_opensre_env_once",
        lambda **_kw: order.append("env"),
    )
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: order.append("sentry"),
    )
    monkeypatch.setattr(
        "infrastructure.observability.trace.otel_sdk.init_otel_tracing",
        lambda: bool(order.append("otel")),
    )
    monkeypatch.setattr(
        "bootstrap.process.install_harness_adapters",
        lambda: order.append("adapters"),
    )
    monkeypatch.setattr(
        "bootstrap.process.install_scheduled_delivery_adapters",
        lambda: order.append("runners"),
    )
    monkeypatch.setattr(
        "infrastructure.safety.sandbox.capabilities.boot_capability_warnings",
        lambda: order.append("caps") or ["curl missing"],
    )
    monkeypatch.setattr(
        "core.llm.internal.preload.preload_llm_clients",
        lambda: order.append("preload"),
    )

    configure_process(GATEWAY_PROFILE, logger=logging.getLogger("test.process"))

    # Outbound instrumentation lands before any client an adapter builds.
    assert order == ["env", "sentry", "otel", "adapters", "caps", "preload"], order


def test_configure_process_cli_boots_env_and_outbound_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI_PROFILE leaves Sentry and Rich adapters to surfaces/cli/startup.

    It does own outbound tracing: integration tools run in the CLI process, so
    without this step their ``requests``/``boto3`` calls produce no spans.
    """
    order: list[str] = []
    monkeypatch.setattr(
        "bootstrap.process.bootstrap_opensre_env_once",
        lambda **_kw: order.append("env"),
    )
    monkeypatch.setattr(
        "infrastructure.observability.trace.otel_sdk.init_otel_tracing",
        lambda: bool(order.append("otel")),
    )
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: order.append("sentry"),
    )
    monkeypatch.setattr(
        "bootstrap.process.install_harness_adapters",
        lambda: order.append("adapters"),
    )
    monkeypatch.setattr(
        "bootstrap.process.install_scheduled_delivery_adapters",
        lambda: order.append("runners"),
    )

    configure_process(CLI_PROFILE)

    assert order == ["env", "otel"], order


def test_configure_process_web_skips_llm_preload(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        "bootstrap.process.bootstrap_opensre_env_once",
        lambda **_kw: order.append("env"),
    )
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: order.append("sentry"),
    )
    monkeypatch.setattr(
        "bootstrap.process.install_harness_adapters",
        lambda: order.append("adapters"),
    )
    monkeypatch.setattr(
        "bootstrap.process.install_scheduled_delivery_adapters",
        lambda: order.append("runners"),
    )
    monkeypatch.setattr(
        "core.llm.internal.preload.preload_llm_clients",
        lambda: order.append("preload"),
    )

    configure_process(WEB_PROFILE)

    assert order == ["env", "sentry", "adapters"], order


def test_configure_process_is_idempotent_per_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def _env(**_kw: Any) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr("bootstrap.process.bootstrap_opensre_env_once", _env)
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: None,
    )
    monkeypatch.setattr("bootstrap.process.install_harness_adapters", lambda: None)
    monkeypatch.setattr("bootstrap.process.install_scheduled_delivery_adapters", lambda: None)

    configure_process(WEB_PROFILE)
    configure_process(WEB_PROFILE)
    assert calls == 1


class TestEmbeddedProfile:
    """The recipe ``main.py`` documents for driving the agent from Python."""

    def test_registers_harness_adapters_so_tools_resolve(self, monkeypatch) -> None:
        # Arrange: without adapter registration the harness starts but no tool
        # resolves, which is the failure an embedder hits with env-only boot.
        from bootstrap import process

        installed: list[str] = []

        def _record(name: str):
            def _step() -> None:
                installed.append(name)

            return _step

        monkeypatch.setattr(process, "install_harness_adapters", _record("adapters"))
        monkeypatch.setattr(process, "install_scheduled_delivery_adapters", _record("runners"))
        monkeypatch.setattr(process, "bootstrap_opensre_env_once", lambda **_kw: None)

        # Act
        process.configure_process(process.EMBEDDED_PROFILE)

        # Assert: adapters yes, scheduler runners no — an embedded host is not
        # running the scheduler.
        assert installed == ["adapters"]

    def test_leaves_error_reporting_to_the_host(self) -> None:
        # Arrange / Act: a library embedded in someone else's process must not
        # install its own Sentry client or preload LLM clients on import.
        from bootstrap.process import EMBEDDED_PROFILE, BootStep

        # Assert
        assert BootStep.SENTRY not in EMBEDDED_PROFILE.steps
        assert BootStep.PRELOAD_LLM not in EMBEDDED_PROFILE.steps
        assert BootStep.SCHEDULER_RUNNERS not in EMBEDDED_PROFILE.steps


def test_gateway_reports_missing_capabilities_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a long-running daemon is the host least able to notice a missing
    # binary later, so its boot warnings are the ones worth pinning. They were
    # dropped once already while converting flags to a step set.
    from bootstrap import process

    warned: list[str] = []
    monkeypatch.setattr(process, "bootstrap_opensre_env_once", lambda **_kw: None)
    monkeypatch.setattr(process, "install_harness_adapters", lambda: None)
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
    process.configure_process(process.GATEWAY_PROFILE, logger=_Log())  # type: ignore[arg-type]

    # Assert
    assert warned == ["gateway kubectl missing"]


def test_scheduler_worker_profile_boots_runners_with_scheduler_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``opensre cron start`` uses SCHEDULER_WORKER_PROFILE (dedicated daemon)."""
    from bootstrap import process

    ran: list[str] = []

    def _record(name: str):
        def _step() -> None:
            ran.append(name)

        return _step

    monkeypatch.setattr(process, "bootstrap_opensre_env_once", lambda **_kw: ran.append("env"))
    monkeypatch.setattr(process, "install_harness_adapters", _record("adapters"))
    monkeypatch.setattr(process, "install_scheduled_delivery_adapters", _record("runners"))
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: ran.append(f"sentry:{_kw.get('entrypoint')}"),
    )

    process.configure_process(process.SCHEDULER_WORKER_PROFILE)

    assert ran == ["env", "sentry:scheduler", "adapters", "runners"]


def test_scheduled_command_profile_dispatches_without_touching_sentry(monkeypatch) -> None:
    # Arrange: cron / digest / report commands run inside an already-booted CLI
    # process. They need the runners that dispatch scheduled work, but Sentry is
    # the CLI's to own (its update path tolerates a missing SDK).
    from bootstrap import process

    ran: list[str] = []

    def _record(name: str):
        def _step() -> None:
            ran.append(name)

        return _step

    monkeypatch.setattr(process, "bootstrap_opensre_env_once", lambda **_kw: ran.append("env"))
    monkeypatch.setattr(process, "install_harness_adapters", _record("adapters"))
    monkeypatch.setattr(process, "install_scheduled_delivery_adapters", _record("runners"))
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kw: ran.append("sentry"),
    )

    # Act
    process.configure_process(process.SCHEDULED_COMMAND_PROFILE)

    # Assert
    assert ran == ["env", "adapters", "runners"]
