"""Tests for phase-1 memory extraction: parsing, provenance gating, scheduling."""

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
from config.constants.skills import ONBOARDING_SKILL_NAME
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope, current_scope
from core.agent_harness.session import memory_turns
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.agent_harness.session.session_core import SessionCore
from core.domain.memory import delete_memory, list_memories, load_memory, memory_dir, save_memory
from core.domain.memory.summaries import recent_session_summaries


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

    def fake_invoke(prompt: str) -> str:
        prompts.append(prompt)
        return response

    monkeypatch.setattr(extraction, "_invoke_extraction_llm", fake_invoke)
    return prompts


def _item(
    name: str = "user-profile",
    *,
    memory_type: str = "user",
    source: str | None = "user",
    evidence: str = "I'm Vaibhav",
    verified: Any = True,
    description: str = "Name is Vaibhav",
    content: str = "The user's name is Vaibhav.",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name,
        "type": memory_type,
        "description": description,
        "content": content,
        "evidence": evidence,
        "verified": verified,
    }
    if source is not None:
        item["source"] = source
    return item


def _response(*items: dict[str, Any], summary: str = "", outcome: str = "success") -> str:
    return json.dumps({"session_summary": summary, "outcome": outcome, "memories": list(items)})


class TestParsing:
    def test_object_response_saves_memories(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, _response(_item()))
        extraction.extract_memories_from_session(_FakeSession())
        assert [r.slug for r in list_memories()] == ["user-profile"]

    def test_fenced_or_prose_wrapped_json_is_tolerated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_llm(monkeypatch, f"Here you go:\n```json\n{_response(_item())}\n```\nDone.")
        extraction.extract_memories_from_session(_FakeSession())
        assert len(list_memories()) == 1

    def test_a_bare_array_is_read_as_memories(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(monkeypatch, json.dumps([_item()]))
        extraction.extract_memories_from_session(_FakeSession())
        assert len(list_memories()) == 1

    @pytest.mark.parametrize(
        "garbage", ["not json", "{}", "[1, 2]", "", '[{"name": 3}]', '{"memories": "x"}']
    )
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
            _item(),
        ]
        _patch_llm(monkeypatch, json.dumps({"memories": items}))
        extraction.extract_memories_from_session(_FakeSession())
        assert [r.slug for r in list_memories()] == ["user-profile"]

    def test_cap_of_five_memories(self, monkeypatch: pytest.MonkeyPatch) -> None:
        items = [_item(f"mem-{i}") for i in range(extraction.MAX_MEMORIES_PER_SESSION + 3)]
        _patch_llm(monkeypatch, _response(*items))
        extraction.extract_memories_from_session(_FakeSession())
        assert len(list_memories()) == extraction.MAX_MEMORIES_PER_SESSION

    def test_session_summary_is_recorded_for_consolidation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_llm(monkeypatch, _response(summary="Set up the prod cluster facts.", outcome="fail"))

        @dataclass
        class _IdentifiedSession(_FakeSession):
            session_id: str = "s-summary"

        extraction.extract_memories_from_session(_IdentifiedSession())

        [summary] = recent_session_summaries()
        assert (summary.session_id, summary.outcome) == ("s-summary", "fail")
        assert summary.text == "Set up the prod cluster facts."


def test_a_session_that_began_before_a_forget_records_no_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its turns may state the forgotten fact, and consolidation reads session summaries."""
    store = JsonlSessionStore()
    store.open_session(SessionCore(session_id="s-forget"))
    store.append_turn_detail("s-forget", "chat", "I work at Acme", response="Noted.", turn_id="t1")
    assert save_memory(slug="employer", memory_type="user", description="Acme", body="Acme")
    assert delete_memory("employer")
    _patch_llm(monkeypatch, _response(summary="The user said where they work."))

    extraction.schedule_memory_extraction(
        [("user", "I work at Acme"), ("assistant", "Noted.")],
        session_id="s-forget",
        wait_for_completion=True,
    )

    assert recent_session_summaries() == []


@dataclass
class _LoggedSession:
    session_id: str
    cli_agent_messages: list[tuple[str, str]]


_CI_REQUEST = "why did CI fail on run 18822?"
#: Evidence the session's tool output supports.
_SHOWN = "github_get_run 18822: failure on windows-latest"


@pytest.fixture
def ci_session() -> _LoggedSession:
    """A logged session whose one turn ran a tool showing run 18822 failed on windows-latest."""
    store = JsonlSessionStore()
    store.open_session(SessionCore(session_id="s-ci"))
    store.append_tool_call(
        "s-ci",
        tool="github_get_run",
        arguments={"run_id": 18822},
        result='{"conclusion": "failure", "job": "windows-latest"}',
        ok=True,
        source="wal",
        sidecar=True,
    )
    reply = "Run 18822 failed on windows-latest; a retry passed."
    store.append_turn_detail("s-ci", "chat", _CI_REQUEST, response=reply, turn_id="t1")
    return _LoggedSession("s-ci", [("user", _CI_REQUEST), ("assistant", reply)])


def _repository_fact(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "memory_type": "repository",
        "source": "tool",
        "evidence": _SHOWN,
        "description": "acme/payments windows-latest job is flaky",
        "content": "Run 18822 failed on windows-latest; a retry passed.",
    }
    fields.update(overrides)
    return _item("repository-acme-payments", **fields)


class TestProvenanceGate:
    def test_a_tool_fact_the_tool_output_shows_is_kept_as_verified(
        self, monkeypatch: pytest.MonkeyPatch, ci_session: _LoggedSession
    ) -> None:
        _patch_llm(monkeypatch, _response(_repository_fact()))
        extraction.extract_memories_from_session(ci_session)

        record = load_memory("repository-acme-payments")
        assert record is not None
        assert (record.source, record.verified, record.evidence) == ("tool", True, _SHOWN)

    @pytest.mark.parametrize(
        ("memory_type", "source", "evidence", "stored"),
        [
            # A quote the user never wrote.
            ("repository", "user", "our CI only fails on windows-latest", None),
            # A run the tool output does not show.
            ("repository", "tool", "gh run view 99999 failed on windows-latest", None),
            # A personal memory is kept, but as the assistant's unverified word.
            ("preference", "user", "always page me about windows-latest", ("assistant", False)),
        ],
    )
    def test_provenance_the_digest_does_not_support_is_never_stored_as_verified(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ci_session: _LoggedSession,
        memory_type: str,
        source: str,
        evidence: str,
        stored: tuple[str, bool] | None,
    ) -> None:
        """The model's own labels used to be trusted, so an invented fact came back verified."""
        _patch_llm(
            monkeypatch,
            _response(_repository_fact(memory_type=memory_type, source=source, evidence=evidence)),
        )
        extraction.extract_memories_from_session(ci_session)

        record = load_memory("repository-acme-payments")
        assert (None if record is None else (record.source, record.verified)) == stored

    @pytest.mark.parametrize(
        ("evidence", "verified"), [("", True), (_SHOWN, False), (_SHOWN, None)]
    )
    def test_tool_facts_need_evidence_and_the_models_verification(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ci_session: _LoggedSession,
        evidence: str,
        verified: Any,
    ) -> None:
        _patch_llm(monkeypatch, _response(_repository_fact(evidence=evidence, verified=verified)))
        extraction.extract_memories_from_session(ci_session)
        assert list_memories() == []

    @pytest.mark.parametrize(
        "memory_type", ["infrastructure", "repository", "investigation_learning"]
    )
    @pytest.mark.parametrize("source", ["assistant", None])
    def test_the_assistants_word_alone_never_backs_a_system_fact(
        self, monkeypatch: pytest.MonkeyPatch, memory_type: str, source: str | None
    ) -> None:
        _patch_llm(
            monkeypatch,
            _response(_item("checkout-api-infra", memory_type=memory_type, source=source)),
        )
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []

    def test_user_statements_are_kept_for_any_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm(
            monkeypatch,
            _response(
                _item(
                    "prod-cluster",
                    memory_type="infrastructure",
                    source="user",
                    evidence="our prod cluster is eks-prod-1",
                    verified=False,
                )
            ),
        )
        extraction.extract_memories_from_session(_FakeSession())
        assert [r.slug for r in list_memories()] == ["prod-cluster"]

    def test_demo_output_is_never_saved_whatever_its_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_llm(
            monkeypatch,
            _response(
                _item(
                    "repository-davincios-opensre-ci-repair-demo-yx06",
                    memory_type="repository",
                    source="user",
                    description="davincios/opensre-ci-repair-demo-yx06 is a CI-repair demo",
                )
            ),
        )
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []

    def test_secret_like_extracted_item_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Construct at runtime so pre-commit secret scanners do not flag fixtures.
        fake_token = "ghp_" + ("a" * 36)
        _patch_llm(
            monkeypatch,
            _response(
                _item("secret-token", memory_type="preference", content=f"auth_token: {fake_token}")
            ),
        )
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []


class TestSkipConditions:
    def test_skips_a_transcript_without_a_complete_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts = _patch_llm(monkeypatch, _response(_item()))
        extraction.extract_memories_from_session(_FakeSession(cli_agent_messages=[("user", "hi")]))
        assert prompts == []

    def test_skips_when_every_turn_was_a_demo_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompts = _patch_llm(monkeypatch, _response(_item()))
        menu_answer = 'What would you like to do?\n@json:"Run one repair in OpenSRE Cloud"'
        memory_turns.note_recorded_turn("s-demo", demo=True, turn_id=None, user_text=menu_answer)

        extraction.schedule_memory_extraction(
            [("user", menu_answer), ("assistant", "Repaired PR #1 in the demo repository.")],
            session_id="s-demo",
            wait_for_completion=True,
        )

        assert prompts == []

    def test_skips_when_autoextract_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompts = _patch_llm(monkeypatch, _response(_item()))
        monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
        extraction.extract_memories_from_session(_FakeSession())
        assert prompts == []

    def test_skips_when_memory_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompts = _patch_llm(monkeypatch, _response(_item()))
        monkeypatch.setenv(OPENSRE_MEMORY_DISABLED_ENV, "1")
        extraction.extract_memories_from_session(_FakeSession())
        assert prompts == []

    def test_llm_failure_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(prompt: str) -> str:
            raise RuntimeError("llm exploded")

        monkeypatch.setattr(extraction, "_invoke_extraction_llm", boom)
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []

    def test_llm_client_unavailable_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _unavailable(_role: object) -> object:
            raise RuntimeError("no settings")

        monkeypatch.setattr("core.llm.factory.get_llm", _unavailable)
        extraction.extract_memories_from_session(_FakeSession())
        assert list_memories() == []


class _FakeLLM:
    def __init__(self, response: str = '{"memories": []}') -> None:
        self.prompts: list[str] = []
        self._response = response

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._response


def test_prompt_carries_the_shared_policy_and_redacts_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.domain.memory import MEMORY_WRITE_POLICY

    llm = _FakeLLM()
    monkeypatch.setattr("core.llm.factory.get_llm", lambda _role: llm)
    fake_token = "ghp_" + ("a" * 36)
    extraction.extract_memories_from_messages(
        [("user", f"auth_token: {fake_token}"), ("assistant", "I will not store that")]
    )

    [prompt] = llm.prompts
    assert MEMORY_WRITE_POLICY in prompt
    assert fake_token not in prompt
    assert "[REDACTED]" in prompt


_WAIT_SECONDS = 5.0


def _transcript(label: str, *, turns: int = 1) -> list[tuple[str, str]]:
    """``turns`` exchanges whose user lines carry ``label`` so a run can be identified."""
    return [message for _ in range(turns) for message in (("user", label), ("assistant", "ok"))]


def _schedule(session_id: str, messages: list[tuple[str, str]]) -> None:
    extraction.schedule_memory_extraction(messages, session_id=session_id)


class _GatedExtractor:
    """Stand-in for ``_extract_memories_safe`` that holds the worker on its first run.

    While the worker is held, later jobs queue behind it, so which ones
    coalesce is decided deterministically instead of racing the worker.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, StorageScope | None]] = []
        self.busy = threading.Event()
        self.release = threading.Event()

    def __call__(self, job: extraction.ExtractionJob) -> None:
        self.calls.append((job.messages[0][1], len(job.messages), current_scope()))
        if not self.busy.is_set():
            self.busy.set()
            self.release.wait(_WAIT_SECONDS)


def _join_worker() -> None:
    """Wait for the coalescing worker to drain every pending job and exit."""
    with extraction._worker_lock:
        worker = extraction._worker
    if worker is not None:
        worker.join(_WAIT_SECONDS)
        assert not worker.is_alive(), "coalescing worker never drained"
    assert extraction._pending == {}


@pytest.fixture
def idle_worker() -> Iterator[None]:
    """Start and end with no coalescing worker running or job pending."""
    _join_worker()
    yield
    _join_worker()


class TestSchedule:
    def test_wait_for_completion_runs_synchronously(self, monkeypatch: pytest.MonkeyPatch) -> None:
        order: list[str] = []

        def _extract(job: extraction.ExtractionJob) -> None:
            order.append(f"extract:{len(job.messages)}:{job.final}")

        monkeypatch.setattr(extraction, "_extract_memories_safe", _extract)
        extraction.schedule_memory_extraction(
            [("user", "hi"), ("assistant", "hello")],
            session_id="s-1",
            wait_for_completion=True,
        )
        assert order == ["extract:2:True"]

    def test_wait_for_completion_waits_past_a_slow_extraction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Close-path extraction must not abandon a slow worker (silent data loss)."""
        import time

        done = threading.Event()

        def _extract(job: extraction.ExtractionJob) -> None:
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
        """Session B's job must not replace session A's unprocessed one.

        A process-wide pending slot let the newest job from any session
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
    def test_same_session_coalesces_to_its_latest_job(
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
    def test_close_supersedes_only_its_own_sessions_pending_job(
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


@pytest.mark.usefixtures("idle_worker")
def test_close_waits_for_the_pass_the_worker_is_running_for_its_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older pass finishing after the final one would overwrite the final pass's memories."""
    order: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def _extract(job: extraction.ExtractionJob) -> None:
        if not job.final:
            started.set()
            release.wait(_WAIT_SECONDS)
        order.append("final" if job.final else "mid-session")

    monkeypatch.setattr(extraction, "_extract_memories_safe", _extract)
    extraction._schedule_coalesced(
        extraction.ExtractionJob(session_id="s-a", messages=tuple(_transcript("mid")))
    )
    assert started.wait(_WAIT_SECONDS)

    closer = threading.Thread(
        target=extraction.schedule_memory_extraction,
        args=(_transcript("final"),),
        kwargs={"session_id": "s-a", "wait_for_completion": True},
    )
    closer.start()
    closer.join(0.2)
    release.set()
    closer.join(_WAIT_SECONDS)

    assert not closer.is_alive()
    assert order == ["mid-session", "final"]


@dataclass
class _TurnSession:
    session_id: str
    cli_agent_messages: list[tuple[str, str]] = field(default_factory=list)
    active_skill: str | None = None


def test_a_queued_pass_keeps_its_demo_fence_after_the_session_record_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A close pass drops the session's turn record; an older pass still running must not
    then read the session's demo turns as ordinary ones."""
    queued: list[extraction.ExtractionJob] = []
    monkeypatch.setattr(extraction, "_schedule_coalesced", queued.append)
    session = _TurnSession(session_id="s-fence", active_skill=ONBOARDING_SKILL_NAME)
    session.cli_agent_messages += [
        ("user", "Run one repair in OpenSRE Cloud"),
        ("assistant", "Repaired PR #1 in octocat/opensre-ci-repair-demo-ab12."),
    ]
    extraction.record_turn_for_memory(session)
    session.active_skill = None
    for index in range(memory_turns.EXTRACTION_TURN_INTERVAL):
        session.cli_agent_messages += [
            ("user", f"our prod cluster is eks-prod-{index}"),
            ("assistant", "noted"),
        ]
        extraction.record_turn_for_memory(session)
    [job] = queued

    memory_turns.forget_session("s-fence")
    prompts = _patch_llm(monkeypatch, _response())
    extraction._extract_memories_safe(job)

    [prompt] = prompts
    assert "eks-prod-5" in prompt
    assert "opensre-ci-repair-demo" not in prompt


def test_scheduled_extraction_thread_inherits_storage_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rotation-path daemon thread must run inside the per-turn storage
    scope. Without contextvars.copy_context() the thread sees current_scope()
    is None and save_memory() misfiles the user's facts under the org root
    instead of users/<actor_id>/memory/ (regression guard)."""
    seen: dict[str, Any] = {}
    done = threading.Event()

    def _recorder(_job: extraction.ExtractionJob) -> None:
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

    def fake_invoke(prompt: str) -> str:
        seen.append(threading.current_thread().name)
        return _response(_item())

    monkeypatch.setattr(extraction, "_invoke_extraction_llm", fake_invoke)

    extraction.schedule_memory_extraction(
        [("user", "hi, I'm Vaibhav"), ("assistant", "hello!")],
        session_id="s-1",
        wait_for_completion=True,
    )

    assert seen == ["opensre-memory-extraction-close"]
    assert threading.current_thread().name not in seen
    assert memory_dir().is_dir()
