"""A turn starts memory consolidation in the background, in the turn's surface and scope."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

import core.agent_harness.session.memory_consolidation as consolidation
from config.constants import (
    OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV,
    OPENSRE_MEMORY_DIR_ENV,
    OPENSRE_MEMORY_DISABLED_ENV,
    OPENSRE_MEMORY_GATEWAY_ENABLED_ENV,
)
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope, current_scope
from core.agent_harness.session import SessionManager
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.turns.orchestrator import ExecuteActions, run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.domain.memory import ConsolidationResult, consolidate_memories, memory_dir, save_memory
from core.domain.memory.files import SUMMARY_FILENAME
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context

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
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "## User\nVaibhav, platform SRE."


class _Accounting:
    def record_action_result(self, _result: ToolCallingTurnResult) -> None:
        return None

    def finalize(self, result: TurnResult) -> TurnResult:
        return result


def _reply(_text: str, **_kwargs: Any) -> ToolCallingTurnResult:
    return ToolCallingTurnResult(1, 1, 1, False, True, response_text="Done.")


def _turn(session: SessionCore, *, surface: str) -> None:
    run_turn(
        "hello",
        session,
        execute_actions=cast(ExecuteActions, _reply),
        accounting=_Accounting(),
        surface=surface,
    )


def _recording_consolidation(
    monkeypatch: pytest.MonkeyPatch,
) -> list[StorageScope | None]:
    """Replace the consolidation run; each call records the storage scope it ran in."""
    seen: list[StorageScope | None] = []

    def _consolidate(**_kwargs: object) -> ConsolidationResult:
        seen.append(current_scope())
        return ConsolidationResult(ran=True)

    monkeypatch.setattr(consolidation, "consolidate_memories", _consolidate)
    return seen


def test_a_turn_writes_the_memory_summary_in_the_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert save_memory(
        slug="user-profile", memory_type="user", description="Vaibhav", body="Name is Vaibhav."
    )
    llm = _FakeLLM()
    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: llm)

    _turn(SessionCore(store=InMemorySessionStore()), surface="interactive_shell")
    _join_consolidation_threads()

    summary = (memory_dir() / SUMMARY_FILENAME).read_text(encoding="utf-8")
    assert "Vaibhav, platform SRE." in summary


@pytest.mark.parametrize("opted_in", [False, True])
def test_a_slack_member_is_consolidated_only_with_the_gateway_opt_in(
    monkeypatch: pytest.MonkeyPatch, memory_home: Path, opted_in: bool
) -> None:
    """Slack resolves a session before it binds its surface; only the turn sees the opt-in.

    The member's folder is left over from a period when the opt-in was on, so
    a check that cannot see the surface would send it to the model.
    """
    memory_home.mkdir(parents=True)
    if opted_in:
        monkeypatch.setenv(OPENSRE_MEMORY_GATEWAY_ENABLED_ENV, "1")
    else:
        monkeypatch.delenv(OPENSRE_MEMORY_GATEWAY_ENABLED_ENV, raising=False)
    seen = _recording_consolidation(monkeypatch)
    scope = StorageScope(principal=Principal.org("org_a"), actor=Actor(id="U1"))

    with bound_storage_scope(scope):
        session = SessionManager().create(hydrate_integrations=False, persistent_tasks=False)
        with bound_usage_context(surface=UsageSurface.SLACK):
            _turn(session, surface="gateway")
    _join_consolidation_threads()

    assert seen == ([scope] if opted_in else [])


def test_frequent_turns_check_the_store_once(
    monkeypatch: pytest.MonkeyPatch, memory_home: Path
) -> None:
    memory_home.mkdir(parents=True)
    seen = _recording_consolidation(monkeypatch)

    for _ in range(5):
        consolidation.start_memory_consolidation()
    _join_consolidation_threads()

    assert len(seen) == 1


def test_a_member_without_a_memory_folder_gets_none_created(
    monkeypatch: pytest.MonkeyPatch, memory_home: Path
) -> None:
    seen = _recording_consolidation(monkeypatch)

    consolidation.start_memory_consolidation()
    _join_consolidation_threads()

    assert seen == []
    assert not memory_home.exists()


def test_a_failing_store_never_reaches_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unreadable() -> Path:
        raise PermissionError("memory volume not mounted")

    monkeypatch.setattr(consolidation, "memory_dir", _unreadable)

    consolidation.start_memory_consolidation()  # must not raise


def test_a_secret_in_a_hand_edited_memory_never_reaches_the_summary_model(
    monkeypatch: pytest.MonkeyPatch, memory_home: Path
) -> None:
    # Construct at runtime so pre-commit secret scanners do not flag fixtures.
    fake_token = "ghp_" + ("a" * 36)
    memory_home.mkdir(parents=True)
    (memory_home / "deploy-notes.md").write_text(
        "---\nname: deploy-notes\ntype: infrastructure\ndescription: Deploy notes\n"
        "created: 2026-10-01T00:00:00+00:00\nupdated: 2026-10-01T00:00:00+00:00\n---\n"
        f"Deploys authenticate with {fake_token} from the vault.\n",
        encoding="utf-8",
    )
    llm = _FakeLLM()
    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: llm)

    assert consolidate_memories(summarize=consolidation.summarize_with_llm).summary_written

    [prompt] = llm.prompts
    assert "Deploys authenticate with" in prompt
    assert fake_token not in prompt
