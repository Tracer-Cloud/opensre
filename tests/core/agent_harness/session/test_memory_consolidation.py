"""Session start runs memory consolidation in the background, in the caller's scope."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

import core.agent_harness.session.memory_consolidation as consolidation
from config.constants import (
    OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV,
    OPENSRE_MEMORY_DIR_ENV,
    OPENSRE_MEMORY_DISABLED_ENV,
)
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope, current_scope
from core.agent_harness.session import SessionManager
from core.agent_harness.session.session_core import SessionCore
from core.domain.memory import ConsolidationResult, memory_dir, save_memory
from core.domain.memory.files import SUMMARY_FILENAME

_WAIT_SECONDS = 10.0


@pytest.fixture(autouse=True)
def memory_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)
    monkeypatch.delenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, raising=False)
    monkeypatch.setattr(consolidation, "_last_attempt", {})
    monkeypatch.setattr(consolidation, "_running", set())
    yield tmp_path / "memory"
    _join_consolidation_threads()


def _join_consolidation_threads() -> None:
    for thread in threading.enumerate():
        if thread.name == "opensre-memory-consolidation":
            thread.join(_WAIT_SECONDS)
            assert not thread.is_alive(), "consolidation thread never finished"


class _FakeLLM:
    def invoke(self, prompt: str) -> str:
        assert "--- Stored memories ---" in prompt
        return "## User\nVaibhav, platform SRE."


def test_bootstrap_writes_the_memory_summary_in_the_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert save_memory(
        slug="user-profile", memory_type="user", description="Vaibhav", body="Name is Vaibhav."
    )
    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: _FakeLLM())

    SessionManager().bootstrap(SessionCore(), hydrate_integrations=False, persistent_tasks=False)
    _join_consolidation_threads()

    summary = (memory_dir() / SUMMARY_FILENAME).read_text(encoding="utf-8")
    assert "Vaibhav, platform SRE." in summary


def test_consolidation_runs_in_the_starting_members_scope(
    monkeypatch: pytest.MonkeyPatch, memory_home: Path
) -> None:
    memory_home.mkdir(parents=True)
    seen: list[StorageScope | None] = []
    done = threading.Event()

    def _consolidate(**_kwargs: object) -> ConsolidationResult:
        seen.append(current_scope())
        done.set()
        return ConsolidationResult(ran=True)

    monkeypatch.setattr(consolidation, "consolidate_memories", _consolidate)
    scope = StorageScope(principal=Principal.org("org_a"), actor=Actor(id="U1"))

    with bound_storage_scope(scope):
        consolidation.start_memory_consolidation()

    assert done.wait(_WAIT_SECONDS)
    assert seen == [scope]


def test_frequent_session_starts_check_the_store_once(
    monkeypatch: pytest.MonkeyPatch, memory_home: Path
) -> None:
    memory_home.mkdir(parents=True)
    calls: list[int] = []

    def _consolidate(**_kwargs: object) -> ConsolidationResult:
        calls.append(1)
        return ConsolidationResult(ran=False)

    monkeypatch.setattr(consolidation, "consolidate_memories", _consolidate)

    for _ in range(5):
        consolidation.start_memory_consolidation()
    _join_consolidation_threads()

    assert calls == [1]


def test_a_member_without_a_memory_folder_gets_none_created(
    monkeypatch: pytest.MonkeyPatch, memory_home: Path
) -> None:
    """A Slack member without the memory opt-in must not get a folder or an LLM call."""
    calls: list[int] = []

    def _consolidate(**_kwargs: object) -> ConsolidationResult:
        calls.append(1)
        return ConsolidationResult(ran=True)

    monkeypatch.setattr(consolidation, "consolidate_memories", _consolidate)

    consolidation.start_memory_consolidation()
    _join_consolidation_threads()

    assert calls == []
    assert not memory_home.exists()


def test_a_failing_store_never_reaches_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unreadable() -> Path:
        raise PermissionError("memory volume not mounted")

    monkeypatch.setattr(consolidation, "memory_dir", _unreadable)

    consolidation.start_memory_consolidation()  # must not raise
