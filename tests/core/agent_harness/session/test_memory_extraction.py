"""Tests for session-end long-term memory extraction."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import core.agent_harness.session.memory_extraction as extraction
from config.constants import (
    OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV,
    OPENSRE_MEMORY_DIR_ENV,
    OPENSRE_MEMORY_DISABLED_ENV,
)
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope, current_scope
from core.domain.memory import list_memories


@pytest.fixture(autouse=True)
def _isolated_memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)
    monkeypatch.delenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, raising=False)


@dataclass
class _FakeSession:
    cli_agent_messages: list[tuple[str, str]] = field(
        default_factory=lambda: [
            ("user", "hi, I'm Vaibhav"),
            ("assistant", "hello!"),
            ("user", "our prod cluster is eks-prod-1"),
            ("assistant", "noted"),
        ]
    )


def _patch_llm(monkeypatch: pytest.MonkeyPatch, response: str) -> list[str]:
    prompts: list[str] = []

    def fake_invoke(messages: list[tuple[str, str]]) -> str:
        prompts.append(str(messages))
        return response

    monkeypatch.setattr(extraction, "_invoke_extraction_llm", fake_invoke)
    return prompts


def _valid_item(name: str = "user-profile") -> dict[str, Any]:
    return {
        "name": name,
        "type": "user",
        "description": "Name is Vaibhav",
        "content": "The user's name is Vaibhav.",
    }


_WAIT_SECONDS = 5.0


def _transcript(label: str, *, turns: int = 1) -> list[tuple[str, str]]:
    """``turns`` exchanges whose user lines carry ``label`` so a run can be identified."""
    return [message for _ in range(turns) for message in (("user", label), ("assistant", "ok"))]


def _schedule(session_id: str, messages: list[tuple[str, str]]) -> None:
    extraction.schedule_memory_extraction(messages, session_id=session_id)


class _GatedExtractor:
    """Stand-in for ``_extract_memories_safe`` that holds the worker on its first run.

    While the worker is held, later snapshots queue behind it, so which ones
    coalesce is decided deterministically instead of racing the worker.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, StorageScope | None]] = []
        self.busy = threading.Event()
        self.release = threading.Event()

    def __call__(self, messages: list[tuple[str, str]]) -> None:
        self.calls.append((messages[0][1], len(messages), current_scope()))
        if not self.busy.is_set():
            self.busy.set()
            self.release.wait(_WAIT_SECONDS)


def _join_worker() -> None:
    """Wait for the coalescing worker to drain every pending snapshot and exit."""
    with extraction._worker_lock:
        worker = extraction._worker
    if worker is not None:
        worker.join(_WAIT_SECONDS)
        assert not worker.is_alive(), "coalescing worker never drained"
    assert extraction._pending == {}


@pytest.fixture
def idle_worker() -> Iterator[None]:
    """Start and end with no coalescing worker running or snapshot pending."""
    _join_worker()
    yield
    _join_worker()


class TestExtraction:
    def test_valid_json_saves_memories(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, json.dumps([_valid_item()]))
        extraction.extract_memories_from_session(_FakeSession())
        assert [r.slug for r in list_memories()] == ["user-profile"]

    def test_one_completed_turn_can_save_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, json.dumps([_valid_item()]))
        extraction.extract_memories_from_session(
            _FakeSession(cli_agent_messages=[("user", "I'm Vaibhav"), ("assistant", "Hi!")])
        )
        assert [r.slug for r in list_memories()] == ["user-profile"]

    def test_fenced_json_tolerated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, f"```json\n{json.dumps([_valid_item()])}\n```")
        extraction.extract_memories_from_session(_FakeSession())
        assert len(list_memories()) == 1

    def test_prose_around_array_tolerated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, f"Here you go:\n{json.dumps([_valid_item()])}\nDone.")
        extraction.extract_memories_from_session(_FakeSession())
        assert len(list_memories()) == 1

    @pytest.mark.parametrize("garbage", ["not json", "{}", "[1, 2]", "", '[{"name": 3}]'])
    def test_garbage_saves_nothing_and_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch, garbage: str
    ) -> None:
        _patch_llm(monkeypatch, garbage)
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []

    def test_invalid_items_skipped_valid_saved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        items = [
            {"name": "bad", "type": "nonsense", "description": "d", "content": "c"},
            {"name": "no-content", "type": "user", "description": "d", "content": " "},
            _valid_item(),
        ]
        _patch_llm(monkeypatch, json.dumps(items))
        extraction.extract_memories_from_session(_FakeSession())
        assert [r.slug for r in list_memories()] == ["user-profile"]

    def test_secret_like_extracted_item_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Construct at runtime so pre-commit secret scanners do not flag fixtures.
        fake_token = "ghp_" + ("a" * 36)
        items = [
            {
                "name": "secret-token",
                "type": "preference",
                "description": "Old auth token",
                "content": f"auth_token: {fake_token}",
            }
        ]
        _patch_llm(monkeypatch, json.dumps(items))
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []

    def test_sample_alert_infra_and_lessons_are_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        items = [
            {
                "name": "checkout-api-incident-2024-pool-exhaustion",
                "type": "investigation_learning",
                "description": (
                    "checkout-api in us-east-1 had 30% HTTP 500s 5min after "
                    "a deploy due to DB connection pool exhaustion"
                ),
                "content": "checkout-api had a generated sample RCA about DB pool exhaustion.",
            },
            {
                "name": "checkout-api-infra",
                "type": "infrastructure",
                "description": (
                    "checkout-api runs in us-east-1; uses a relational DB with a connection pool"
                ),
                "content": "checkout-api infrastructure notes from a sample alert.",
            },
        ]
        _patch_llm(monkeypatch, json.dumps(items))
        extraction.extract_memories_from_messages(
            [
                ("user", "run a sample alert investigation"),
                (
                    "assistant",
                    "Sample RCA: checkout-api in us-east-1 had HTTP 500s after deploy.",
                ),
            ]
        )
        assert list_memories() == []

    def test_assistant_only_infrastructure_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_llm(
            monkeypatch,
            json.dumps(
                [
                    {
                        "name": "checkout-api-infra",
                        "type": "infrastructure",
                        "description": "checkout-api runs in us-east-1",
                        "content": "checkout-api runs in us-east-1.",
                    }
                ]
            ),
        )
        extraction.extract_memories_from_messages(
            [
                ("user", "what happened?"),
                ("assistant", "checkout-api runs in us-east-1."),
            ]
        )
        assert list_memories() == []

    def test_user_grounded_infrastructure_still_saves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_llm(
            monkeypatch,
            json.dumps(
                [
                    {
                        "name": "prod-cluster",
                        "type": "infrastructure",
                        "description": "prod cluster is eks-prod-1",
                        "content": "The user's prod cluster is eks-prod-1.",
                    }
                ]
            ),
        )
        extraction.extract_memories_from_messages(
            [
                ("user", "our prod cluster is eks-prod-1"),
                ("assistant", "got it"),
            ]
        )
        assert [r.slug for r in list_memories()] == ["prod-cluster"]

    def test_user_grounded_repositories_save_as_separate_memories(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_llm(
            monkeypatch,
            json.dumps(
                [
                    {
                        "name": "repo-tracer-cloud-opensre",
                        "type": "repository",
                        "description": "OpenSRE repository",
                        "content": "Tracer-Cloud/opensre uses main as its default branch.",
                    },
                    {
                        "name": "repo-acme-payments",
                        "type": "repository",
                        "description": "Payments repository",
                        "content": "acme/payments deploys checkout-api from release.",
                    },
                ]
            ),
        )

        extraction.extract_memories_from_messages(
            [
                (
                    "user",
                    "Tracer-Cloud/opensre uses main; acme/payments deploys checkout-api from release.",
                ),
                ("assistant", "Got it."),
            ]
        )

        assert {record.slug for record in list_memories()} == {
            "repo-tracer-cloud-opensre",
            "repo-acme-payments",
        }

    def test_cap_of_five_memories(self, monkeypatch: pytest.MonkeyPatch) -> None:
        items = [_valid_item(f"mem-{i}") for i in range(extraction.MAX_MEMORIES_PER_SESSION + 3)]
        _patch_llm(monkeypatch, json.dumps(items))
        extraction.extract_memories_from_session(_FakeSession())
        assert len(list_memories()) == extraction.MAX_MEMORIES_PER_SESSION


class TestSkipConditions:
    def test_skips_short_sessions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompts = _patch_llm(monkeypatch, json.dumps([_valid_item()]))
        session = _FakeSession(
            cli_agent_messages=[("user", "hi")] * (extraction.MIN_CHAT_MESSAGES - 1)
        )
        extraction.extract_memories_from_session(session)
        assert prompts == []
        assert list_memories() == []

    def test_skips_when_autoextract_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompts = _patch_llm(monkeypatch, json.dumps([_valid_item()]))
        monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
        extraction.extract_memories_from_session(_FakeSession())
        assert prompts == []

    def test_skips_when_memory_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompts = _patch_llm(monkeypatch, json.dumps([_valid_item()]))
        monkeypatch.setenv(OPENSRE_MEMORY_DISABLED_ENV, "1")
        extraction.extract_memories_from_session(_FakeSession())
        assert prompts == []

    def test_llm_failure_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(messages: list[tuple[str, str]]) -> str:
            raise RuntimeError("llm exploded")

        monkeypatch.setattr(extraction, "_invoke_extraction_llm", boom)
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []

    def test_llm_client_unavailable_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "core.llm.factory.get_llm",
            lambda _role: (_ for _ in ()).throw(RuntimeError("no settings")),
        )
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []


class TestSchedule:
    def test_wait_for_completion_runs_synchronously(self, monkeypatch: pytest.MonkeyPatch) -> None:
        order: list[str] = []

        def _extract(messages: list[tuple[str, str]]) -> None:
            order.append(f"extract:{len(messages)}")

        monkeypatch.setattr(extraction, "_extract_memories_safe", _extract)
        extraction.schedule_memory_extraction(
            [("user", "hi"), ("assistant", "hello")],
            session_id="s-1",
            wait_for_completion=True,
        )
        assert order == ["extract:2"]

    def test_wait_for_completion_waits_past_a_slow_extraction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Close-path extraction must not abandon a slow worker (silent data loss)."""
        import time

        done = threading.Event()

        def _extract(messages: list[tuple[str, str]]) -> None:
            time.sleep(0.4)
            done.set()

        monkeypatch.setattr(extraction, "_extract_memories_safe", _extract)
        started = time.monotonic()
        extraction.schedule_memory_extraction(
            [("user", "hi"), ("assistant", "hello")],
            session_id="s-1",
            wait_for_completion=True,
        )
        assert done.is_set()
        assert time.monotonic() - started >= 0.35

    @pytest.mark.usefixtures("idle_worker")
    def test_back_to_back_sessions_are_both_extracted_in_their_own_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Session B's snapshot must not replace session A's unprocessed one.

        A process-wide pending slot let the newest snapshot from any session
        overwrite another session's, silently losing that session's memories.
        """
        gated = _GatedExtractor()
        monkeypatch.setattr(extraction, "_extract_memories_safe", gated)
        _schedule("s-busy", _transcript("busy"))
        assert gated.busy.wait(_WAIT_SECONDS)

        scope_a = StorageScope(principal=Principal.org("org_a"), actor=Actor(id="U-A"))
        scope_b = StorageScope(principal=Principal.org("org_a"), actor=Actor(id="U-B"))
        with bound_storage_scope(scope_a):
            _schedule("s-a", _transcript("from-a"))
        with bound_storage_scope(scope_b):
            _schedule("s-b", _transcript("from-b"))
        gated.release.set()
        _join_worker()

        assert gated.calls[1:] == [("from-a", 2, scope_a), ("from-b", 2, scope_b)]

    @pytest.mark.usefixtures("idle_worker")
    def test_same_session_coalesces_to_its_latest_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gated = _GatedExtractor()
        monkeypatch.setattr(extraction, "_extract_memories_safe", gated)
        _schedule("s-busy", _transcript("busy"))
        assert gated.busy.wait(_WAIT_SECONDS)

        _schedule("s-a", _transcript("from-a", turns=1))
        _schedule("s-a", _transcript("from-a", turns=2))
        gated.release.set()
        _join_worker()

        assert [(label, size) for label, size, _scope in gated.calls] == [
            ("busy", 2),
            ("from-a", 4),
        ]

    @pytest.mark.usefixtures("idle_worker")
    def test_close_supersedes_only_its_own_sessions_pending_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gated = _GatedExtractor()
        monkeypatch.setattr(extraction, "_extract_memories_safe", gated)
        _schedule("s-busy", _transcript("busy"))
        assert gated.busy.wait(_WAIT_SECONDS)

        _schedule("s-a", _transcript("from-a"))
        _schedule("s-b", _transcript("from-b"))
        extraction.schedule_memory_extraction(
            _transcript("a-final", turns=2), session_id="s-a", wait_for_completion=True
        )
        gated.release.set()
        _join_worker()

        assert [label for label, _size, _scope in gated.calls] == ["busy", "a-final", "from-b"]

    def test_transcript_is_redacted_before_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompts: list[str] = []
        fake_token = "ghp_" + ("a" * 36)

        class _FakeLLM:
            def invoke(self, prompt: str) -> str:
                prompts.append(prompt)
                return "[]"

        monkeypatch.setattr(
            "core.llm.factory.get_llm",
            lambda _role: _FakeLLM(),
        )
        extraction.extract_memories_from_messages(
            [
                ("user", f"auth_token: {fake_token}"),
                ("assistant", "I will not store that"),
            ]
        )
        assert len(prompts) == 1
        assert fake_token not in prompts[0]
        assert "[REDACTED]" in prompts[0]


def test_scheduled_extraction_thread_inherits_storage_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rotation-path daemon thread must run inside the per-turn storage
    scope. Without contextvars.copy_context() the thread sees current_scope()
    is None and save_memory() misfiles the user's facts under the org root
    instead of users/<actor_id>/memory/ (regression guard)."""
    monkeypatch.setattr(extraction, "auto_extract_enabled", lambda: True)

    seen: dict[str, Any] = {}
    done = threading.Event()

    def _recorder(_messages: list[tuple[str, str]]) -> None:
        seen["scope"] = current_scope()
        done.set()

    monkeypatch.setattr(extraction, "_extract_memories_safe", _recorder)

    scope = StorageScope(principal=Principal.org("org_a"), actor=Actor(id="U1"))
    with bound_storage_scope(scope):
        extraction.schedule_memory_extraction(
            [("user", "hi"), ("assistant", "hello")],
            session_id="s-1",
            wait_for_completion=False,
        )

    assert done.wait(timeout=5), "extraction thread never ran"
    assert seen["scope"] is scope


def test_close_extraction_runs_off_the_main_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    # Session-close extraction must run its blocking LLM call in the bounded
    # daemon thread, never on the main thread — otherwise a slow provider hangs
    # shutdown and a Ctrl+C during exit crashes through the network read.
    seen: list[str] = []

    def fake_invoke(messages: list[tuple[str, str]]) -> str:
        seen.append(threading.current_thread().name)
        return json.dumps([_valid_item()])

    monkeypatch.setattr(extraction, "_invoke_extraction_llm", fake_invoke)

    extraction.schedule_memory_extraction(
        [("user", "hi, I'm Vaibhav"), ("assistant", "hello!")],
        session_id="s-1",
        wait_for_completion=True,
    )

    assert seen == ["opensre-memory-extraction-close"]
    assert threading.current_thread().name not in seen
