"""Tests for the prompt worker: what a remote turn may do and how it settles."""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from config.constants.gateway import PROMPT_CONVERSATION_NEW, PROMPT_SLOT_WAIT_SECONDS
from core.agent_harness import SessionCore, SessionManager
from core.agent_harness.session import InMemorySessionStore
from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from gateway.core.prompt_intake import (
    ERROR_CANCELLED,
    ERROR_CONVERSATION_WAITING,
    ERROR_CREDITS_DENIED,
    ERROR_INVALID_ANSWER,
    ERROR_NOT_ADMITTED,
    ERROR_TURN_FAILED,
    ERROR_UNKNOWN_CONVERSATION,
    JsonlPromptJobStore,
    PromptJob,
    PromptQueue,
    PromptState,
    PromptWorker,
)
from infrastructure.turn_host.capability_policy import ensure_gateway_capability_policy
from infrastructure.turn_host.unattended_session import (
    UnattendedSessions,
    prepare_unattended_session,
)

_LOGGER = logging.getLogger("test")
#: Upper bound on any wait in a threaded test; reached only when the test fails.
_WAIT = 10.0


class _Handler:
    """A fake turn callback that records what the worker gave it."""

    def __init__(self, *, answer: str = "", asks: PendingUserChoice | None = None) -> None:
        self.answer = answer
        self.asks = asks
        self.seen_text = ""
        self.seen_capabilities: dict[str, tuple[str, ...]] = {}
        self.result: Any = object()
        self.dropped: list[str] = []

    def run(self, text: str, session: SessionCore, output: Any, _logger: Any, **kwargs: Any) -> Any:
        self.seen_text = text
        self.seen_kwargs = dict(kwargs)
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


def test_a_remote_turn_defers_questions_and_gets_the_context_as_facts() -> None:
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
    assert handler.seen_capabilities == {"ask_user_choice": ("deferred",)}
    assert handler.seen_text.endswith("Known context:\n- repository: Tracer-Cloud/opensre")
    # Accepted work waits for a turn slot instead of failing the moment a chat turn runs.
    assert handler.seen_kwargs == {"slot_wait_seconds": PROMPT_SLOT_WAIT_SECONDS}


_CRON_ADD_COMMAND = (
    "/cron add --kind manual_loop --cron '0 9 * * *' --provider slack --prompt 'Check CI.'"
)


@pytest.mark.parametrize(
    "command",
    [_CRON_ADD_COMMAND, f"[Slack thread context] {_CRON_ADD_COMMAND}"],
    ids=["plain", "vendor-prefixed"],
)
def test_a_literal_command_reaches_the_turn_without_the_facts(command: str) -> None:
    """Facts appended to ``/cron add …`` reached the command as extra arguments and failed it."""
    # Arrange: the CLI client adds the bound GitHub connection to every prompt's context
    handler = _Handler(answer="Task created.")
    worker, queue = _worker(handler)
    job = queue.submit(command, context={"github_connection_id": "42"}, actor="user_1")
    assert job is not None

    # Act
    worker.run_one(job)

    # Assert
    assert handler.seen_text == command


class _ToolCatalogHandler(_Handler):
    """Resolve the real prompt and scheduled-tick tool catalogs on each turn."""

    def __init__(self) -> None:
        super().__init__(asks=PendingUserChoice(title="Which loop?", options=("demo",)))
        self.prompt_tools: list[set[str]] = []
        self.tick_tools: list[set[str]] = []

    def run(self, text: str, session: SessionCore, output: Any, logger: Any, **kwargs: Any) -> Any:
        ensure_gateway_capability_policy(session, hosts_scheduler=True)
        for unattended, snapshots in ((False, self.prompt_tools), (True, self.tick_tools)):
            provider = DefaultToolProvider(session, console=None, unattended=unattended)
            tools = provider.action_tools(
                confirm_fn=None,
                is_tty=False,
                resolved_integrations={"github": {"token": "test-token"}},
            )
            snapshots.append({tool.name for tool in tools})
        return super().run(text, session, output, logger, **kwargs)


def test_remote_prompt_tools_survive_resume_without_enabling_controls_on_scheduled_ticks() -> None:
    handler = _ToolCatalogHandler()
    worker, queue = _worker(handler)
    job = queue.submit("enable a paused loop", context={}, actor="u")
    assert job is not None

    worker.run_one(job)
    handler.asks = None
    follow_up = queue.answer(job, "demo")
    assert follow_up is not None
    worker.run_one(follow_up)

    assert follow_up.state is PromptState.DONE
    assert len(handler.prompt_tools) == len(handler.tick_tools) == 2
    for names in handler.prompt_tools:
        assert {
            "slash_invoke",
            "schedule_ci_repair_loop",
            "list_scheduled_loops",
            "cli_exec",
            "llm_set_provider",
            "task_cancel",
        } <= names
        assert names.isdisjoint(
            {
                "ask_hosted_gateway",
                "check_hosted_gateway",
                "start_hosted_gateway",
                "stop_hosted_gateway",
            }
        )
    for names in handler.tick_tools:
        assert names.isdisjoint(
            {
                "slash_invoke",
                "schedule_ci_repair_loop",
                "ask_hosted_gateway",
                "check_hosted_gateway",
                "start_hosted_gateway",
                "stop_hosted_gateway",
            }
        )


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


def test_deferred_questions_leave_other_capabilities_alone() -> None:
    # Arrange
    session = SessionCore()
    session.available_capabilities["something_else"] = ("on",)

    # Act
    prepare_unattended_session(session)

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

    def run_and_fail_tools(
        _text: str, _session: SessionCore, output: Any, _logger: Any, **_kwargs: Any
    ) -> Any:
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


def test_a_tool_refusal_is_not_a_failed_integration() -> None:
    """A tool declining on its own rules must not send the user to the credential checklist."""
    from core.llm.types import ToolCall
    from core.tool import ERROR_KIND_REFUSED, ToolExecutionRequest, ToolExecutionResult
    from tools.registry import clear_tool_registry_cache, get_registered_tool_map

    # Arrange: one github tool refuses, another github tool really fails
    clear_tool_registry_cache()
    registered = get_registered_tool_map()
    handler = _Handler(answer="the pull request was refused")

    def run_with_a_refusal(
        _text: str, _session: SessionCore, output: Any, _logger: Any, **_kwargs: Any
    ) -> Any:
        request = ToolExecutionRequest(
            tool_call=ToolCall(id="c", name="github_cli", input={}),
            tool=registered["github_cli"],
            arguments={},
            source="test",
            resolved_integrations={},
        )
        refused = ToolExecutionResult(
            content="refused",
            details={"ok": False, "error": "refused", "error_kind": ERROR_KIND_REFUSED},
            is_error=True,
        )
        output.tool_hooks.after_tool_call(request, refused)
        output.finalize(handler.answer)
        return handler.result

    handler.run = run_with_a_refusal  # type: ignore[method-assign, assignment]
    worker, queue = _worker(handler)
    job = queue.submit("repair PR 6404", context={}, actor="u")
    assert job is not None

    # Act
    worker.run_one(job)

    # Assert
    assert job.state is PromptState.DONE
    assert job.failed_integrations == ()


class _GatedTool:
    """A stand-in write tool. No product tool still sets ``requires_approval``."""

    requires_approval = True
    approval_reason = "Starts a worker."
    input_schema = {
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "pr_number": {"type": "integer"},
            "confirm": {"type": "boolean", "default": False},
        },
    }


def _approval_request(pr_number: int = 7, *, default_spelled_out: bool = False) -> Any:
    from core.llm.types import ToolCall
    from core.tool import ToolExecutionRequest

    arguments: dict[str, Any] = {"owner": "o", "repo": "r", "pr_number": pr_number}
    if default_spelled_out:
        arguments["confirm"] = False
    return ToolExecutionRequest(
        tool_call=ToolCall(id="c", name="gated_tool", input={}),
        tool=_GatedTool(),
        arguments=arguments,
        source="test",
        resolved_integrations={},
    )


class _ApprovalHandler(_Handler):
    """A turn that tries approval-required calls and records the hook's verdicts."""

    def __init__(self, pr_numbers: list[int]) -> None:
        super().__init__(answer="scheduled")
        self.pr_numbers = pr_numbers
        self.spell_out_demo = False
        self.verdicts: list[Any] = []

    def run(
        self, text: str, _session: SessionCore, output: Any, _logger: Any, **_kwargs: Any
    ) -> Any:
        self.seen_text = text
        for pr_number in self.pr_numbers:
            request = _approval_request(pr_number, default_spelled_out=self.spell_out_demo)
            verdict = output.tool_hooks.before_tool_call(request)
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
    assert asked.question.startswith("Approve gated_tool?")
    assert "pr_number" in asked.question
    assert same is None
    assert again.blocked is True and other.blocked is True and other.terminate is True
    assert follow_up.state is PromptState.NEEDS_INPUT
    assert follow_up.session_id == asked.session_id
    assert "Approve" in handler.seen_text and "gated_tool" in handler.seen_text


def test_an_approval_survives_the_model_restating_the_call_without_its_defaults() -> None:
    """A grant covers the same call restated without its schema-default arguments."""
    # Arrange: the first turn spells the default out; the resumed turn leaves it out
    handler = _ApprovalHandler([7])
    handler.spell_out_demo = True
    worker, queue = _worker(handler)
    asked = queue.submit("schedule the repair loop for o/r#7", context={}, actor="u")
    assert asked is not None

    # Act
    worker.run_one(asked)
    handler.spell_out_demo = False
    follow_up = queue.answer(asked, "Approve")
    assert follow_up is not None
    worker.run_one(follow_up)

    # Assert: the restated call is the approved call; no second question is asked
    first, resumed = handler.verdicts
    assert first.blocked is True
    assert resumed is None
    assert follow_up.state is PromptState.DONE


class _HistoryHandler(_Handler):
    """A turn that records the transcript the worker seeded before it ran."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seen_history: list[tuple[str, str]] = []

    def run(self, text: str, session: SessionCore, output: Any, logger: Any, **kwargs: Any) -> Any:
        self.seen_history = list(session.cli_agent_messages or [])
        return super().run(text, session, output, logger, **kwargs)


def test_a_resumed_turn_is_seeded_with_the_request_and_the_question() -> None:
    # Arrange: a parent that asked; the unattended session keeps no transcript
    asks = PendingUserChoice(title="Which PR?", options=("7", "8"))
    handler = _HistoryHandler(answer="done", asks=asks)
    worker, queue = _worker(handler)
    parent = queue.submit("schedule the repair loop", context={"repo": "o/r"}, actor="u")
    assert parent is not None
    worker.run_one(parent)
    assert parent.state is PromptState.NEEDS_INPUT

    # Act: the answer resumes the session
    handler.asks = None
    follow_up = queue.answer(parent, "7")
    assert follow_up is not None
    worker.run_one(follow_up)

    # Assert: the agent sees the request (with its facts), the question, then the answer
    assert follow_up.state is PromptState.DONE
    assert handler.seen_history[0][0] == "user"
    assert "schedule the repair loop" in handler.seen_history[0][1]
    assert "repo: o/r" in handler.seen_history[0][1]
    assert handler.seen_history[1] == ("assistant", parent.question)
    assert "7" in handler.seen_text


def test_a_session_is_retired_only_when_the_queue_holds_none_of_its_prompts() -> None:
    # Arrange: a parent that asked, then a follow-up that asks again on the same session
    class _Clock:
        def __init__(self) -> None:
            self.now = 1_000.0

        def read(self) -> float:
            return self.now

    clock = _Clock()
    branch = PendingUserChoice(title="Which branch?", options=("main", "release"))
    handler = _Handler(asks=branch)
    queue = PromptQueue(retention_seconds=60.0, clock=clock.read)
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

    def run(
        self, text: str, session: SessionCore, output: Any, _logger: Any, **_kwargs: Any
    ) -> Any:
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

    def run(
        self, text: str, _session: SessionCore, output: Any, _logger: Any, **_kwargs: Any
    ) -> Any:
        self.seen_text = text
        output.set_tool_status("Reading workflow runs…")
        output.render_response_header("Assistant")
        output.set_tool_status("Checking out the branch…")
        output.finalize(self.answer)
        return self.result


class _ActivityHandler(_Handler):
    """A turn that reports compact hosted activity the way the observer does."""

    def run(
        self, text: str, _session: SessionCore, output: Any, _logger: Any, **_kwargs: Any
    ) -> Any:
        self.seen_text = text
        output.note_activity("GitHub CLI · gh api user", kind="tool")
        checklist = "Plan · 1/2\n  ● List orgs\n  ○ Check permission"
        output.note_activity(checklist, kind="plan")
        output.note_activity(checklist, kind="plan")
        output.render_plan_breakdown("Plan complete · 2/2\n  ✓ List orgs\n  ✓ Check permission")
        output.finalize(self.answer)
        return self.result


def test_hosted_activity_reaches_the_job_with_its_kind() -> None:
    handler = _ActivityHandler(answer="done")
    worker, queue = _worker(handler)
    job = queue.submit("fix ci", context={}, actor="u")
    assert job is not None

    worker.run_one(job)

    progress = job.view()["progress"]
    assert [item["kind"] for item in progress] == ["tool", "plan", "plan_done"]
    assert progress[0]["text"] == "GitHub CLI · gh api user"
    assert "✓ Check permission" in progress[2]["text"]
    assert job.state is PromptState.DONE


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


def test_a_second_question_is_seeded_with_the_request_and_the_first_answer() -> None:
    # Arrange: the store keeps no transcript; the agent asks twice before it finishes
    first = PendingUserChoice(title="Which PR?", options=("7", "8"))
    second = PendingUserChoice(title="Force push?", options=("yes", "no"))
    handler = _HistoryHandler(answer="done", asks=first)
    worker, queue = _worker(handler)
    root = queue.submit("schedule the repair loop", context={"repo": "o/r"}, actor="u")
    assert root is not None
    worker.run_one(root)
    handler.asks = second
    answered_once = queue.answer(root, "7")
    assert answered_once is not None
    worker.run_one(answered_once)
    assert answered_once.state is PromptState.NEEDS_INPUT

    # Act: the second answer resumes the session
    handler.asks = None
    answered_twice = queue.answer(answered_once, "no")
    assert answered_twice is not None
    worker.run_one(answered_twice)

    # Assert: request, first question, first answer, second question; the new answer is the turn
    assert answered_twice.state is PromptState.DONE
    history = list(handler.seen_history)
    assert history[0][0] == "user" and "schedule the repair loop" in history[0][1]
    assert history[1] == ("assistant", root.question)
    assert history[2] == ("user", "7")
    assert history[3] == ("assistant", answered_once.question)
    assert len(history) == 4
    assert "no" in handler.seen_text


def test_a_forgotten_original_request_still_leaves_the_parents_question_seeded() -> None:
    # Arrange: two questions; the original request has expired from the queue
    handler = _HistoryHandler(
        answer="done", asks=PendingUserChoice(title="Which PR?", options=("7",))
    )
    worker, queue = _worker(handler)
    root = queue.submit("schedule the repair loop", context={}, actor="u")
    assert root is not None
    worker.run_one(root)
    handler.asks = PendingUserChoice(title="Force push?", options=("yes", "no"))
    once = queue.answer(root, "7")
    assert once is not None
    worker.run_one(once)
    del queue._jobs[root.id]  # the retention window dropped the original request

    # Act
    handler.asks = None
    twice = queue.answer(once, "no")
    assert twice is not None
    worker.run_one(twice)

    # Assert: the known part is seeded, starting at the parent's own question
    assert twice.state is PromptState.DONE
    assert handler.seen_history == [("assistant", once.question)]


class _TranscriptHandler(_Handler):
    def __init__(self) -> None:
        super().__init__(answer="recorded")
        self.transcripts: list[list[tuple[str, str]]] = []

    def run(self, text: str, session: SessionCore, output: Any, logger: Any, **kwargs: Any) -> Any:
        self.transcripts.append(list(session.cli_agent_messages))
        session.record("chat", text)
        session.cli_agent_messages.append(("user", text))
        session.cli_agent_messages.append(("assistant", self.answer))
        return super().run(text, session, output, logger, **kwargs)


def test_hosted_conversation_survives_worker_restart_and_isolates_actors_and_organizations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv("OPENSRE_CONTEXT_ROOT", raising=False)
    monkeypatch.setattr(
        "gateway.core.prompt_intake.worker.bound_turn_metering", lambda **_kwargs: nullcontext()
    )
    handler = _TranscriptHandler()
    session_ids = []
    for org, actor, prompt in (
        ("org-a", "alice", "first request"),
        ("org-a", "bob", "another actor"),
        ("org-b", "alice", "another organization"),
        ("org-a", "alice", "continue original request"),
    ):
        monkeypatch.setenv("ORGANIZATION_ID", org)
        # Recreate the worker and session manager, as a replacement container does.
        queue = PromptQueue()
        worker = PromptWorker(queue, handler, logger=_LOGGER)
        job = queue.submit(prompt, context={}, actor=actor)
        assert job is not None
        worker.run_one(job)
        assert job.state is PromptState.DONE
        session_ids.append(job.session_id)

    assert handler.transcripts[:3] == [[], [], []]
    assert handler.transcripts[3] == [("user", "first request"), ("assistant", "recorded")]
    assert session_ids[0] == session_ids[3]
    assert len(set(session_ids)) == 3
    assert not (tmp_path / "sessions").exists()


class _PersistentApprovalHandler(_ApprovalHandler):
    def run(self, text: str, session: SessionCore, output: Any, logger: Any, **kwargs: Any) -> Any:
        session.record("chat", text)
        session.cli_agent_messages.append(("user", text))
        return super().run(text, session, output, logger, **kwargs)


def test_new_hosted_requests_do_not_replace_an_awaiting_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv("OPENSRE_CONTEXT_ROOT", raising=False)
    monkeypatch.setenv("ORGANIZATION_ID", "org-a")
    monkeypatch.setattr(
        "gateway.core.prompt_intake.worker.bound_turn_metering", lambda **_kwargs: nullcontext()
    )
    handler = _PersistentApprovalHandler([7])
    queue = PromptQueue()
    worker = PromptWorker(queue, handler, logger=_LOGGER)
    first = queue.submit("repair o/r#7", context={}, actor="alice")
    second = queue.submit("repair o/r#8", context={}, actor="alice")
    assert first is not None and second is not None
    worker.run_one(first)
    handler.pr_numbers = [8]
    worker.run_one(second)

    assert first.state is second.state is PromptState.NEEDS_INPUT
    assert first.session_id != second.session_id
    handler.pr_numbers = [7]
    answer = queue.answer(first, "Approve")
    assert answer is not None
    worker.run_one(answer)
    assert handler.verdicts[-1] is None
    assert answer.state is PromptState.DONE

    handler.pr_numbers = [8]
    answer = queue.answer(second, "Approve")
    assert answer is not None
    worker.run_one(answer)
    assert handler.verdicts[-1] is None
    assert answer.state is PromptState.DONE


class _ParkingHandler(_Handler):
    """A turn that records itself on the session, so the store keeps it, and may ask."""

    def __init__(self, *, asks: PendingUserChoice | None = None) -> None:
        super().__init__(answer="pushed to release", asks=asks)
        self.seen_session_ids: list[str] = []

    def run(self, text: str, session: SessionCore, output: Any, _logger: Any, **kwargs: Any) -> Any:
        self.seen_session_ids.append(session.session_id)
        session.record("chat", text)
        session.cli_agent_messages.append(("user", text))
        return super().run(text, session, output, _logger, **kwargs)


def test_a_parked_question_is_answered_on_its_session_after_the_gateway_task_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: an org silo whose prompt parks on a question that accepts only its options
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv("OPENSRE_CONTEXT_ROOT", raising=False)
    monkeypatch.setenv("ORGANIZATION_ID", "org-a")
    monkeypatch.setattr(
        "gateway.core.prompt_intake.worker.bound_turn_metering", lambda **_kwargs: nullcontext()
    )
    records = tmp_path / "prompt-jobs.jsonl"
    branch = PendingUserChoice(
        title="Which branch?", options=("main", "release"), custom_answer=False
    )
    first_task = _ParkingHandler(asks=branch)
    queue = PromptQueue(store=JsonlPromptJobStore(records))
    asked = queue.submit("fix ci", context={}, actor="alice")
    assert asked is not None
    PromptWorker(queue, first_task, logger=_LOGGER).run_one(asked)
    assert asked.state is PromptState.NEEDS_INPUT

    # Act: a new task with a fresh queue, worker and session manager over the same volume
    second_task = _ParkingHandler()
    restarted = PromptQueue(store=JsonlPromptJobStore(records))
    worker = PromptWorker(restarted, second_task, logger=_LOGGER)
    parked = restarted.get(asked.id)
    assert parked is not None
    read_after_restart = parked.view()
    follow_up = restarted.answer(parked, "2")
    assert follow_up is not None
    worker.run_one(follow_up)

    # Assert: the poller still reads the question, and option 2 resolves against it on the
    # same session — so the question came back from the store rather than being dropped
    assert read_after_restart == asked.view()
    assert parked.view()["answered_by"] == follow_up.id
    assert follow_up.state is PromptState.DONE
    assert second_task.seen_session_ids == [asked.session_id]
    assert second_task.seen_text.startswith("1. Which branch?")
    assert '"release"' in second_task.seen_text


class _SignallingQueue(PromptQueue):
    """Lets a threaded test wait for prompts to settle or defer instead of sleeping."""

    def __init__(self) -> None:
        super().__init__()
        self.settled = threading.Semaphore(0)
        self.deferred = threading.Event()

    def finish(self, job: PromptJob, answer: str, **kwargs: Any) -> None:
        super().finish(job, answer, **kwargs)
        self.settled.release()

    def needs_input(self, job: PromptJob, question: str, **kwargs: Any) -> None:
        super().needs_input(job, question, **kwargs)
        self.settled.release()

    def fail(self, job: PromptJob, error_code: str, **kwargs: Any) -> None:
        super().fail(job, error_code, **kwargs)
        self.settled.release()

    def defer(self, job: PromptJob, session_id: str) -> None:
        super().defer(job, session_id)
        self.deferred.set()

    def wait_settled(self, count: int) -> None:
        for _ in range(count):
            assert self.settled.acquire(timeout=_WAIT)


class _BlockingHandler(_Handler):
    """Holds the first ``held`` turns together until released; records the order turns start."""

    def __init__(self, held: int) -> None:
        super().__init__(answer="ok")
        self.held = held
        # The held turns plus the test thread, so the test knows all of them are running.
        self.together = threading.Barrier(held + 1, timeout=_WAIT)
        self.release = threading.Event()
        self.started: list[str] = []
        self._lock = threading.Lock()

    def run(self, text: str, session: SessionCore, output: Any, logger: Any, **kwargs: Any) -> Any:
        with self._lock:
            self.started.append(text)
            held = len(self.started) <= self.held
        if held:
            self.together.wait()
            assert self.release.wait(_WAIT)
        return super().run(text, session, output, logger, **kwargs)


def test_workers_run_other_conversations_at_once_and_one_conversation_in_order() -> None:
    # Arrange: alice's own conversation twice, bob's, and a separate one of alice's
    handler = _BlockingHandler(held=3)
    queue = _SignallingQueue()
    sessions = UnattendedSessions(SessionManager(store=InMemorySessionStore()))
    worker = PromptWorker(queue, handler, logger=_LOGGER, sessions=sessions, workers=3)
    first = queue.submit("alice 1", context={}, actor="alice")
    second = queue.submit("alice 2", context={}, actor="alice")
    queue.submit("bob", context={}, actor="bob")
    queue.submit("alice new", context={}, actor="alice", conversation=PROMPT_CONVERSATION_NEW)
    assert first is not None and second is not None

    # Act
    worker.start()
    try:
        handler.together.wait()
        waiting = second.state
        handler.release.set()
        queue.wait_settled(4)
    finally:
        stopped = worker.stop(timeout_seconds=_WAIT)

    # Assert: three conversations ran at once; alice's second prompt ran after her first
    assert set(handler.started[:3]) == {"alice 1", "bob", "alice new"}
    assert waiting is PromptState.QUEUED and handler.started[3] == "alice 2"
    assert first.state is second.state is PromptState.DONE
    assert stopped


class _StoppableHandler(_Handler):
    """A turn that parks a question, then runs until its cancel event is set."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()

    def run(self, text: str, session: SessionCore, output: Any, _logger: Any, **_kw: Any) -> Any:
        session.pending_user_choice = PendingUserChoice(title="Which branch?", options=("main",))
        session.record("chat", text)
        self.started.set()
        assert output.turn_cancel.wait(_WAIT)
        return self.result


def test_a_cancelled_running_prompt_settles_cancelled_and_leaves_no_question_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv("OPENSRE_CONTEXT_ROOT", raising=False)
    handler = _StoppableHandler()
    queue = _SignallingQueue()
    sessions = UnattendedSessions(SessionManager())
    worker = PromptWorker(queue, handler, logger=_LOGGER, sessions=sessions)
    job = queue.submit("fix ci", context={}, actor="u")
    assert job is not None

    # Act
    worker.start()
    try:
        assert handler.started.wait(_WAIT)
        queue.cancel(job)
        queue.wait_settled(1)
    finally:
        worker.stop(timeout_seconds=_WAIT)

    # Assert: no parked question holds the conversation for an answer that never comes
    assert (job.state, job.error_code) == (PromptState.FAILED, ERROR_CANCELLED)
    assert sessions.resume(job.session_id).pending_user_choice is None
    assert handler.dropped == [job.session_id]


class _AnswerHandler(_Handler):
    """Asks on the first turn; the answer's turn runs until cancelled unless ``let_through``."""

    def __init__(self) -> None:
        super().__init__(answer="pushed")
        self.answering = threading.Event()
        self.let_through = False

    def run(self, text: str, session: SessionCore, output: Any, logger: Any, **kwargs: Any) -> Any:
        session.record("chat", text)
        if not self.seen_text:
            self.asks = PendingUserChoice(title="Which branch?", options=("main", "release"))
            return super().run(text, session, output, logger, **kwargs)
        self.asks = None
        if not self.let_through:
            self.answering.set()
            assert output.turn_cancel.wait(_WAIT)
        return super().run(text, session, output, logger, **kwargs)


def test_a_cancelled_running_answer_gives_its_question_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: a prompt parked on a question, and an answer that is now running
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv("OPENSRE_CONTEXT_ROOT", raising=False)
    handler = _AnswerHandler()
    queue = _SignallingQueue()
    worker = PromptWorker(queue, handler, logger=_LOGGER, sessions=UnattendedSessions())
    asked = queue.submit("fix ci", context={}, actor="u")
    assert asked is not None

    # Act
    worker.start()
    try:
        queue.wait_settled(1)
        answer = queue.answer(asked, "main")
        assert answer is not None and handler.answering.wait(_WAIT)
        queue.cancel(answer)
        queue.wait_settled(1)
        handler.let_through = True
        again = queue.answer(asked, "release")
        assert again is not None
        queue.wait_settled(1)
    finally:
        worker.stop(timeout_seconds=_WAIT)

    # Assert: the question took a second answer, and it resolved against the question
    assert (answer.state, answer.error_code) == (PromptState.FAILED, ERROR_CANCELLED)
    assert again.state is PromptState.DONE and asked.answered_by == again.id
    assert '"release"' in handler.seen_text


def _org_silo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv("OPENSRE_CONTEXT_ROOT", raising=False)
    monkeypatch.setenv("ORGANIZATION_ID", "org-a")
    monkeypatch.setattr(
        "gateway.core.prompt_intake.worker.bound_turn_metering", lambda **_kwargs: nullcontext()
    )


def _run(
    worker: PromptWorker, queue: PromptQueue, prompt: str, actor: str, conversation: str = ""
) -> PromptJob:
    job = queue.submit(prompt, context={}, actor=actor, conversation=conversation)
    assert job is not None
    worker.run_one(job)
    return job


def test_a_caller_picks_a_new_conversation_or_continues_one_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    _org_silo(tmp_path, monkeypatch)
    handler = _TranscriptHandler()
    queue = PromptQueue()
    worker = PromptWorker(queue, handler, logger=_LOGGER)

    # Act
    own = _run(worker, queue, "first", "alice")
    separate = _run(worker, queue, "separate", "alice", PROMPT_CONVERSATION_NEW)
    own_again = _run(worker, queue, "own again", "alice")
    continued = _run(worker, queue, "continue separate", "alice", separate.session_id)
    foreign = _run(worker, queue, "read alice's", "bob", separate.session_id)
    unknown = _run(worker, queue, "unknown", "alice", str(uuid.uuid4()))

    # Assert: a new conversation leaves the actor's own in place; another actor's id resolves
    # to nothing
    assert separate.session_id != own.session_id == own_again.session_id
    assert continued.session_id == separate.session_id
    assert handler.transcripts[1] == []
    assert handler.transcripts[2] == [("user", "first"), ("assistant", "recorded")]
    assert handler.transcripts[3] == [("user", "separate"), ("assistant", "recorded")]
    assert separate.view()["conversation_id"] == separate.session_id
    assert foreign.error_code == unknown.error_code == ERROR_UNKNOWN_CONVERSATION
    assert len(handler.transcripts) == 4


def test_a_named_conversation_waiting_on_a_question_refuses_a_new_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: a separate conversation parked on a question
    _org_silo(tmp_path, monkeypatch)
    handler = _ParkingHandler(asks=PendingUserChoice(title="Which branch?", options=("main",)))
    queue = PromptQueue()
    worker = PromptWorker(queue, handler, logger=_LOGGER)
    asked = _run(worker, queue, "fix ci", "alice", PROMPT_CONVERSATION_NEW)
    assert asked.state is PromptState.NEEDS_INPUT

    # Act
    refused = _run(worker, queue, "something else", "alice", asked.session_id)
    handler.asks = None
    answer = queue.answer(asked, "main")
    assert answer is not None
    worker.run_one(answer)

    # Assert: the question still takes its answer on that conversation
    assert refused.error_code == ERROR_CONVERSATION_WAITING
    assert answer.state is PromptState.DONE
    assert handler.seen_session_ids == [asked.session_id, asked.session_id]


class _HoldingHandler(_TranscriptHandler):
    """Logs when each turn starts and ends, holding the turn for ``hold`` until released."""

    def __init__(self, hold: str, log: list[tuple[str, str]]) -> None:
        super().__init__()
        self.hold = hold
        self.log = log
        self.holding = threading.Event()
        self.release = threading.Event()

    def run(self, text: str, session: SessionCore, output: Any, logger: Any, **kwargs: Any) -> Any:
        self.log.append(("start", text))
        if text == self.hold:
            self.holding.set()
            assert self.release.wait(_WAIT)
        self.log.append(("end", text))
        return super().run(text, session, output, logger, **kwargs)


class _LoggingSessions(UnattendedSessions):
    """Logs every read of a persisted session."""

    def __init__(self, log: list[tuple[str, str]]) -> None:
        super().__init__()
        self.log = log

    def resume(self, session_id: str) -> SessionCore:
        self.log.append(("resume", session_id))
        return super().resume(session_id)


def test_a_prompt_whose_own_conversation_is_named_by_a_running_prompt_waits_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: alice's own conversation exists from before a restart, so this task does
    # not know it yet; one prompt names it, the next continues it by default
    _org_silo(tmp_path, monkeypatch)
    log: list[tuple[str, str]] = []
    handler = _HoldingHandler(hold="named", log=log)
    earlier_queue = PromptQueue()
    own = _run(
        PromptWorker(earlier_queue, handler, logger=_LOGGER), earlier_queue, "first", "alice"
    )
    queue = _SignallingQueue()
    worker = PromptWorker(queue, handler, logger=_LOGGER, sessions=_LoggingSessions(log), workers=2)
    named = queue.submit("named", context={}, actor="alice", conversation=own.session_id)
    by_default = queue.submit("by default", context={}, actor="alice")
    assert named is not None and by_default is not None
    log.clear()

    # Act
    worker.start()
    try:
        assert handler.holding.wait(_WAIT)
        assert queue.deferred.wait(_WAIT)
        handler.release.set()
        queue.wait_settled(2)
    finally:
        worker.stop(timeout_seconds=_WAIT)

    # Assert: the waiting prompt never read the session while the named prompt ran on it,
    # then ran on that same session
    session_id = own.session_id
    assert log == [
        ("resume", session_id),
        ("start", "named"),
        ("end", "named"),
        ("resume", session_id),
        ("start", "by default"),
        ("end", "by default"),
    ]
    assert by_default.session_id == session_id and by_default.state is PromptState.DONE
