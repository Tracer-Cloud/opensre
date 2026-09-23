"""Tests for the prompt worker: what a remote turn may do and how it settles."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from core.agent_harness import SessionCore, SessionManager
from core.agent_harness.session import InMemorySessionStore
from core.agent_harness.session.pending_choice import PendingUserChoice
from gateway.core.prompt_intake import (
    ERROR_CREDITS_DENIED,
    ERROR_INVALID_ANSWER,
    ERROR_NOT_ADMITTED,
    ERROR_TURN_FAILED,
    PromptQueue,
    PromptState,
    PromptWorker,
)
from infrastructure.turn_host.unattended_session import UnattendedSessions, restrict_to_unattended

_LOGGER = logging.getLogger("test")


class _Handler:
    """A fake turn callback that records what the worker gave it."""

    def __init__(self, *, answer: str = "", asks: PendingUserChoice | None = None) -> None:
        self.answer = answer
        self.asks = asks
        self.seen_text = ""
        self.seen_capabilities: dict[str, tuple[str, ...]] = {}
        self.result: Any = object()
        self.dropped: list[str] = []

    def run(self, text: str, session: SessionCore, output: Any, _logger: Any) -> Any:
        self.seen_text = text
        self.seen_capabilities = dict(session.available_capabilities)
        if self.asks is not None:
            session.pending_user_choice = self.asks
        elif self.result is None:
            return None
        else:
            output.finalize(self.answer)
        return self.result

    def drop_session(self, session_id: str) -> None:
        self.dropped.append(session_id)


def _worker(handler: _Handler) -> tuple[PromptWorker, PromptQueue]:
    queue = PromptQueue()
    sessions = UnattendedSessions(SessionManager(store=InMemorySessionStore()))
    worker = PromptWorker(queue, handler, logger=_LOGGER, sessions=sessions)
    return worker, queue


@pytest.fixture(autouse=True)
def _no_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORGANIZATION_ID", raising=False)


def test_a_remote_turn_cannot_ask_or_switch_runtime_and_gets_the_context_as_facts() -> None:
    # Arrange
    handler = _Handler(answer="3 scheduled tasks are running.")
    worker, queue = _worker(handler)
    job = queue.submit(
        "Which scheduled tasks run on this gateway?",
        context={"repository": "Tracer-Cloud/opensre"},
        actor="user_1",
    )
    assert job is not None

    # Act
    worker.run_one(job)

    # Assert
    assert job.state is PromptState.DONE and job.answer == "3 scheduled tasks are running."
    assert handler.seen_capabilities["ask_user_choice"] == ("deferred",)
    assert handler.seen_capabilities["slash_commands"] == ()
    assert handler.seen_capabilities["llm_provider"] == ()
    assert handler.seen_capabilities["cli_commands"] == ()
    assert handler.seen_capabilities["hosted_gateway"] == ()
    assert handler.seen_text.endswith("Known context:\n- repository: Tracer-Cloud/opensre")


def test_a_question_ends_the_turn_as_needs_input_with_the_question_as_text() -> None:
    # Arrange
    pending = PendingUserChoice(title="Which branch?", options=("main", "release"))
    worker, queue = _worker(_Handler(asks=pending))
    job = queue.submit("fix ci", context={}, actor="user_1")
    assert job is not None

    # Act
    worker.run_one(job)

    # Assert
    assert job.state is PromptState.NEEDS_INPUT
    assert job.question == "Which branch?\nOptions: main, release"
    assert job.view()["choice"] == {
        "title": "Which branch?",
        "note": "",
        "questions": [
            {"title": "Which branch?", "options": ["main", "release"], "multi_select": False}
        ],
        "custom_answer": True,
    }


def test_a_rejected_admission_and_a_failed_turn_become_stable_codes() -> None:
    # Arrange
    not_admitted = _Handler()
    not_admitted.result = None

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("provider exploded: secret detail")

    worker_a, queue_a = _worker(not_admitted)
    exploding = _Handler()
    exploding.run = explode  # type: ignore[method-assign]
    worker_b, queue_b = _worker(exploding)
    job_a = queue_a.submit("a", context={}, actor="u")
    job_b = queue_b.submit("b", context={}, actor="u")
    assert job_a is not None and job_b is not None

    # Act
    worker_a.run_one(job_a)
    worker_b.run_one(job_b)

    # Assert: codes only, no exception text reaches the caller's view.
    assert job_a.view()["error"] == ERROR_NOT_ADMITTED
    assert job_b.view()["error"] == ERROR_TURN_FAILED
    assert "secret detail" not in str(job_b.view())
    assert ERROR_CREDITS_DENIED == "credits_denied"


def test_restriction_leaves_other_capabilities_alone() -> None:
    # Arrange
    session = SessionCore()
    session.available_capabilities["something_else"] = ("on",)

    # Act
    restrict_to_unattended(session)

    # Assert
    assert session.available_capabilities["something_else"] == ("on",)


def test_a_failing_integration_tool_is_named_on_the_settled_job() -> None:
    # Arrange: a turn in which a GitHub tool fails and a non-integration tool fails too
    from core.llm.types import ToolCall
    from core.tool import ToolExecutionRequest, ToolExecutionResult
    from tools.registry import clear_tool_registry_cache, get_registered_tool_map

    clear_tool_registry_cache()
    registered = get_registered_tool_map()
    handler = _Handler(answer="could not read the repository")
    seen_hooks: list[Any] = []

    def run_and_fail_tools(_text: str, _session: SessionCore, output: Any, _logger: Any) -> Any:
        seen_hooks.append(output.tool_hooks)
        for name in ("github_cli", "shell_run", "github_cli"):
            request = ToolExecutionRequest(
                tool_call=ToolCall(id="c", name=name, input={}),
                tool=registered[name],
                arguments={},
                source="test",
                resolved_integrations={},
            )
            output.tool_hooks.after_tool_call(
                request, ToolExecutionResult(content="boom", is_error=True)
            )
        output.finalize(handler.answer)
        return handler.result

    handler.run = run_and_fail_tools  # type: ignore[method-assign, assignment]
    worker, queue = _worker(handler)
    job = queue.submit("count open PRs", context={}, actor="u")
    assert job is not None

    # Act
    worker.run_one(job)

    # Assert: only the integration vendor is reported, once, and it reaches the caller's view.
    assert job.state is PromptState.DONE
    assert job.failed_integrations == ("github",)
    assert job.view()["failed_integrations"] == ["github"]


def _approval_request(pr_number: int = 7) -> Any:
    from core.llm.types import ToolCall
    from core.tool import ToolExecutionRequest
    from tools.registry import clear_tool_registry_cache, get_registered_tool_map

    clear_tool_registry_cache()
    tool = get_registered_tool_map()["schedule_ci_repair_loop"]
    assert tool.requires_approval
    return ToolExecutionRequest(
        tool_call=ToolCall(id="c", name="schedule_ci_repair_loop", input={}),
        tool=tool,
        arguments={"owner": "o", "repo": "r", "pr_number": pr_number},
        source="test",
        resolved_integrations={},
    )


class _ApprovalHandler(_Handler):
    """A turn that tries approval-required calls and records the hook's verdicts."""

    def __init__(self, pr_numbers: list[int]) -> None:
        super().__init__(answer="scheduled")
        self.pr_numbers = pr_numbers
        self.verdicts: list[Any] = []

    def run(self, text: str, _session: SessionCore, output: Any, _logger: Any) -> Any:
        self.seen_text = text
        for pr_number in self.pr_numbers:
            verdict = output.tool_hooks.before_tool_call(_approval_request(pr_number))
            self.verdicts.append(verdict)
        output.finalize(self.answer)
        return self.result


def test_an_approval_covers_exactly_the_previewed_call_once() -> None:
    # Arrange: the first turn asks for PR 7; the resumed turn tries PR 7 twice, then PR 8
    handler = _ApprovalHandler([7])
    worker, queue = _worker(handler)
    asked = queue.submit("schedule the repair loop for o/r#7", context={}, actor="u")
    assert asked is not None

    # Act
    worker.run_one(asked)
    handler.pr_numbers = [7, 7, 8]
    follow_up = queue.answer(asked, "Approve")
    assert follow_up is not None
    worker.run_one(follow_up)

    # Assert: the ask ends the turn; only the approved call runs, once; the rest ask again
    first, same, again, other = handler.verdicts
    assert first.blocked is True and first.terminate is True
    assert asked.state is PromptState.NEEDS_INPUT
    assert asked.view()["choice"]["questions"][0]["options"] == ["Approve", "Deny"]
    assert asked.question.startswith("Approve schedule_ci_repair_loop?")
    assert "pr_number" in asked.question
    assert same is None
    assert again.blocked is True and other.blocked is True and other.terminate is True
    assert follow_up.state is PromptState.NEEDS_INPUT
    assert follow_up.session_id == asked.session_id
    assert "Approve" in handler.seen_text and "schedule_ci_repair_loop" in handler.seen_text


def test_a_session_is_retired_only_when_the_queue_holds_none_of_its_prompts() -> None:
    # Arrange: a parent that asked, then a follow-up that asks again on the same session
    class _Clock:
        now = 1_000.0

        def __call__(self) -> float:
            return self.now

    clock = _Clock()
    branch = PendingUserChoice(title="Which branch?", options=("main", "release"))
    handler = _Handler(asks=branch)
    queue = PromptQueue(retention_seconds=60.0, clock=clock)
    sessions = UnattendedSessions(SessionManager(store=InMemorySessionStore()))
    worker = PromptWorker(queue, handler, logger=_LOGGER, sessions=sessions)
    parent = queue.submit("fix ci", context={}, actor="u")
    assert parent is not None
    worker.run_one(parent)
    clock.now += 30.0
    follow_up = queue.answer(parent, "main")
    assert follow_up is not None
    handler.asks = PendingUserChoice(title="Force push?", options=("yes", "no"))
    worker.run_one(follow_up)

    # Act: the parent expires first, the follow-up 30 seconds later
    clock.now += 31.0
    worker.retire_forgotten()
    dropped_after_parent = list(handler.dropped)
    clock.now += 30.0
    worker.retire_forgotten()

    # Assert: the follow-up's question survives its parent; the session goes when both are gone
    assert follow_up.state is PromptState.NEEDS_INPUT
    assert dropped_after_parent == [] and queue.get(parent.id) is None
    assert handler.dropped == [parent.session_id]


def test_an_answer_that_fits_no_option_fails_the_follow_up_and_reopens_the_question() -> None:
    # Arrange
    pending = PendingUserChoice(
        title="Which branch?", options=("main", "release"), custom_answer=False
    )
    handler = _Handler(asks=pending)
    worker, queue = _worker(handler)
    asked = queue.submit("fix ci", context={}, actor="u")
    assert asked is not None
    worker.run_one(asked)

    # Act
    wrong = queue.answer(asked, "develop")
    assert wrong is not None
    worker.run_one(wrong)
    handler.asks = None
    right = queue.answer(asked, "2")
    assert right is not None
    worker.run_one(right)

    # Assert: the bad answer settles as a code; the question could be answered again
    assert wrong.view()["error"] == ERROR_INVALID_ANSWER
    assert right.state is PromptState.DONE
    assert handler.seen_text.startswith("1. Which branch?") and '"release"' in handler.seen_text


class _ReloadingHandler(_Handler):
    """A turn that, like the real runner, loads the session again from its store."""

    def __init__(self) -> None:
        super().__init__(answer="Tracer-Cloud/opensre it is.")
        self.reloaded_pending: list[Any] = []

    def run(self, text: str, session: SessionCore, output: Any, _logger: Any) -> Any:
        self.seen_text = text
        reloaded = SessionManager().resolve(session.session_id, warm_integrations=False)
        self.reloaded_pending.append(reloaded.pending_user_choice)
        output.finalize(self.answer)
        return self.result


def test_an_answered_question_is_gone_from_the_store_before_the_resumed_turn_runs() -> None:
    # Arrange: the real on-disk store, a question parked by the first turn
    handler = _Handler(asks=PendingUserChoice(title="Which repository?", options=("a/b", "c/d")))
    queue = PromptQueue()
    sessions = UnattendedSessions(SessionManager())
    worker = PromptWorker(queue, handler, logger=_LOGGER, sessions=sessions)
    asked = queue.submit("schedule a loop", context={}, actor="u")
    assert asked is not None
    worker.run_one(asked)
    reloading = _ReloadingHandler()
    worker = PromptWorker(queue, reloading, logger=_LOGGER, sessions=sessions)
    worker._asked[asked.session_id] = PendingUserChoice(
        title="Which repository?", options=("a/b", "c/d")
    )

    # Act
    follow_up = queue.answer(asked, "1")
    assert follow_up is not None
    worker.run_one(follow_up)

    # Assert: a fresh load during the turn sees no question, so nothing re-asks it
    assert reloading.reloaded_pending == [None]
    assert follow_up.state is PromptState.DONE


class _NoisyHandler(_Handler):
    """A turn that reports tool progress the way the pooled agent's observer does."""

    def run(self, text: str, _session: SessionCore, output: Any, _logger: Any) -> Any:
        self.seen_text = text
        output.set_tool_status("Reading workflow runs…")
        output.render_response_header("Assistant")
        output.set_tool_status("Checking out the branch…")
        output.finalize(self.answer)
        return self.result


def test_tool_progress_reaches_the_job_while_it_runs() -> None:
    # Arrange
    handler = _NoisyHandler(answer="done")
    worker, queue = _worker(handler)
    job = queue.submit("fix ci", context={}, actor="u")
    assert job is not None

    # Act
    worker.run_one(job)

    # Assert: every status line is recorded in order, and the record still settles as done
    assert [item["text"] for item in job.view()["progress"]] == [
        "Reading workflow runs…",
        "Assistant",
        "Checking out the branch…",
    ]
    assert job.state is PromptState.DONE
