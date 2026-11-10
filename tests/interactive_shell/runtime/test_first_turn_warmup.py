"""The first turn's client and credit read are warmed while the menu waits."""

from __future__ import annotations

import pytest

import core.agent_harness.runtime as harness_runtime
import core.llm.hosted_credits as hosted_credits
from surfaces.interactive_shell.runtime.startup import first_turn_warmup
from surfaces.interactive_shell.runtime.startup.first_turn_warmup import (
    warm_first_turn,
    warm_sdk_imports,
)


def test_warmup_builds_the_client_and_reads_credits_even_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[str] = []

    def build() -> None:
        ran.append("client")
        raise RuntimeError("Missing LLM credentials.")

    monkeypatch.setattr(harness_runtime, "default_llm_factory", build)
    monkeypatch.setattr(hosted_credits, "prefetch_hosted_credits", lambda: ran.append("credits"))

    thread = warm_first_turn()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert thread.daemon
    assert ran == ["client", "credits"]


def test_warmup_prewarms_the_clients_request_path(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    class _Client:
        def prewarm(self) -> None:
            ran.append("prewarm")

    def build() -> _Client:
        ran.append("client")
        return _Client()

    monkeypatch.setattr(harness_runtime, "default_llm_factory", build)
    monkeypatch.setattr(hosted_credits, "prefetch_hosted_credits", lambda: ran.append("credits"))

    thread = warm_first_turn()
    thread.join(timeout=5)

    assert ran == ["client", "prewarm", "credits"]


def test_shutdown_waits_a_bounded_time_for_a_stuck_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    from surfaces.interactive_shell.runtime.startup.first_turn_warmup import (
        join_first_turn_warmup,
    )

    release = threading.Event()
    monkeypatch.setattr(harness_runtime, "default_llm_factory", lambda: release.wait(5))
    monkeypatch.setattr(hosted_credits, "prefetch_hosted_credits", lambda: None)
    thread = warm_first_turn()

    started = time.monotonic()
    join_first_turn_warmup(timeout=0.2)

    assert time.monotonic() - started < 1.0
    assert thread.is_alive()
    release.set()
    thread.join(timeout=5)


def test_sdk_warmup_imports_the_first_turns_modules_on_a_daemon_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported: list[str] = []

    def _import(name: str) -> None:
        imported.append(name)
        if name == "openai.resources.responses":
            raise ImportError("not installed here")

    monkeypatch.setattr(first_turn_warmup.importlib, "import_module", _import)

    thread = warm_sdk_imports()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert thread.daemon
    # Every module is tried; one that is missing is left for the first turn to report.
    assert imported == list(first_turn_warmup._SDK_MODULES)


def test_sdk_warmup_names_modules_that_exist() -> None:
    import importlib

    for name in first_turn_warmup._SDK_MODULES:
        importlib.import_module(name)
