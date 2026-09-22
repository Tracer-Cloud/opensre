"""ReAct goal gates: host rejects stay on; same-LLM review is opt-in."""

from __future__ import annotations

from typing import Any

import pytest

from config.constants.llm import OPENSRE_REACT_GOAL_LLM_REVIEW_ENV
from core.agent.goals import GoalObservation
from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
from core.agent_harness.turns.action_driver import (
    _deferred_reply_presenter,
    _goal_review_user_request,
)
from core.agent_harness.turns.goal_review import (
    build_gather_goal_reviewer,
    build_goal_reviewer,
)
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.llm.types import AgentLLMResponse


class _ScriptedLLM:
    model_id = "test"

    def __init__(self, content: str) -> None:
        self.content = content
        self.invokes = 0

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        _ = tools
        return []

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (messages, system, tools)
        self.invokes += 1
        return AgentLLMResponse(content=self.content)


def _obs(*, text: str = "done", evidence: int = 1) -> GoalObservation:
    return GoalObservation(
        final_text=text,
        evidence_count=evidence,
        iteration=1,
        max_iterations=4,
    )


def test_goal_reviewer_rejects_while_task_plan_incomplete() -> None:
    """Do not idle with Plan · n/m and a mid-list ● — keep the turn going."""
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "check checkout latency",
        executed_tool_names=["update_plan", "call_mcp_tool"],
        plan_incomplete=lambda: True,
    )
    assert goal.verify is not None
    assert goal.verify(_obs()) is False
    assert llm.invokes == 0  # deterministic plan gate — no LLM spend
    assert goal.nudge is not None
    assert "unfinished steps" in goal.nudge(_obs())


def test_goal_reviewer_shows_a_plan_deferred_reply_before_nudging() -> None:
    """A mid-plan report is a step's deliverable: paint it, then say it was shown.

    Observed live: the CI analytics card asked for the report as a text-only
    reply followed by a menu step; the plan gate rejected the reply and the
    accepted conclusion was empty, so the report never reached the screen.
    """
    shown: list[str] = []
    awaits_reply = True
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')

    def _present(text: str) -> bool:
        shown.append(text)
        return True

    goal = build_goal_reviewer(
        llm,
        "analyze CI reliability",
        executed_tool_names=["update_plan", "analyze_github_ci_reliability"],
        plan_incomplete=lambda: True,
        plan_awaits_reply=lambda: awaits_reply,
        on_plan_deferred_reply=_present,
    )
    assert goal.verify is not None and goal.nudge is not None

    assert goal.verify(_obs(text="| Metric | repo |\n|---|---:|")) is False
    nudge = goal.nudge(_obs(text="| Metric | repo |\n|---|---:|"))

    assert shown == ["| Metric | repo |\n|---|---:|"]
    assert nudge.startswith("Your last reply has been shown")
    assert "unfinished steps" in nudge
    # An empty conclusion has nothing to show and must not claim otherwise.
    assert goal.nudge(_obs(text="   ")) == goal.nudge(_obs(text=""))
    assert not goal.nudge(_obs(text="")).startswith("Your last reply")
    assert shown == ["| Metric | repo |\n|---|---:|"]
    # Without the plan's explicit deliverable signal a rejected reply is a
    # premature stop: it stays off the screen and the model is not told otherwise.
    awaits_reply = False
    nudge = goal.nudge(_obs(text="All done, the report is ready."))
    assert shown == ["| Metric | repo |\n|---|---:|"]
    assert not nudge.startswith("Your last reply has been shown")
    assert "unfinished steps" in nudge
    assert llm.invokes == 0


def test_deferred_reply_presenter_records_only_replies_that_reached_the_sink() -> None:
    """A failed stream must not mark the turn streamed, or the reply is lost."""
    delivered: list[str] = []

    class _BrokenSink(BufferOutputSink):
        def stream(self, *_args: Any, **_kwargs: Any) -> None:
            raise OSError("terminal went away")

    working = BufferOutputSink()
    assert _deferred_reply_presenter(working, delivered)("| Metric |") is True
    assert delivered == ["| Metric |"]
    assert working.streamed == ["| Metric |"]

    assert _deferred_reply_presenter(_BrokenSink(), delivered)("| Lost |") is False
    assert delivered == ["| Metric |"]


def test_goal_reviewer_does_not_claim_a_reply_was_shown_when_presenting_failed() -> None:
    """A presenter that could not paint the reply must not make the nudge say it did."""
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "analyze CI reliability",
        executed_tool_names=["update_plan"],
        plan_incomplete=lambda: True,
        plan_awaits_reply=lambda: True,
        on_plan_deferred_reply=lambda _text: False,
    )
    assert goal.nudge is not None

    nudge = goal.nudge(_obs(text="| Metric | repo |"))

    assert not nudge.startswith("Your last reply has been shown")
    assert "unfinished steps" in nudge


def test_goal_reviewer_lets_an_active_goal_redirect_over_a_stale_plan() -> None:
    """A leftover plan must not pull a redirected /goal turn back into it."""
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "how are you doing?",
        executed_tool_names=["shell_run"],
        plan_incomplete=lambda: True,
    )
    assert goal.verify is not None
    assert goal.verify(_obs(text="Doing well.")) is True
    assert llm.invokes == 0


def test_goal_reviewer_lets_an_unrelated_turn_conclude_over_a_stale_plan() -> None:
    """A plan left from an earlier request does not pull this turn back into it."""
    # Arrange: the plan is unfinished, but this turn never touched it.
    llm = _ScriptedLLM('{"verdict": "NOT_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "how are you doing?",
        executed_tool_names=["call_mcp_tool"],
        plan_incomplete=lambda: True,
    )
    assert goal.verify is not None and goal.nudge is not None

    # Act
    accepted = goal.verify(_obs(text="Doing well."))
    nudge = goal.nudge(_obs(text="Doing well."))

    # Assert: no plan rejection, no plan nudge, no LLM spend.
    assert accepted is True
    assert "unfinished steps" not in nudge
    assert llm.invokes == 0


def test_goal_reviewer_ignores_plan_gate_when_complete() -> None:
    llm = _ScriptedLLM('{"verdict": "NOT_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "check checkout latency",
        executed_tool_names=["call_mcp_tool"],
        plan_incomplete=lambda: False,
    )
    assert goal.verify is not None
    assert goal.verify(_obs()) is True
    assert llm.invokes == 0


def test_goal_reviewer_opt_in_llm_still_accepts_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENSRE_REACT_GOAL_LLM_REVIEW_ENV, "1")
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "check checkout latency",
        executed_tool_names=["call_mcp_tool"],
        plan_incomplete=lambda: False,
    )
    assert goal.verify is not None
    assert goal.verify(_obs()) is True
    assert llm.invokes == 1


def test_task_plan_blocks_conclusion_helpers() -> None:
    from core.agent_harness.task_plan.conclusion import task_plan_blocks_conclusion
    from core.agent_harness.task_plan.plan import parse_task_plan

    incomplete, _ = parse_task_plan(
        {
            "plan": [
                {"step": "Discover", "status": "completed"},
                {"step": "Query", "status": "in_progress"},
                {"step": "Verify", "status": "pending"},
            ]
        }
    )
    done, _ = parse_task_plan(
        {
            "plan": [
                {"step": "Discover", "status": "completed"},
                {"step": "Verify", "status": "completed"},
            ]
        }
    )
    # Blocked steps are terminal: the gate must not force fake work to close them.
    settled, _ = parse_task_plan(
        {
            "plan": [
                {"step": "Discover", "status": "completed"},
                {"step": "Query", "status": "blocked"},
                {"step": "Verify", "status": "blocked"},
            ],
            "explanation": "Query blocked: the runtime has no metrics source.",
        }
    )
    assert incomplete is not None and done is not None and settled is not None
    assert task_plan_blocks_conclusion(task_plan=incomplete, plan_only=False) is True
    assert task_plan_blocks_conclusion(task_plan=incomplete, plan_only=True) is False
    assert task_plan_blocks_conclusion(task_plan=done, plan_only=False) is False
    assert task_plan_blocks_conclusion(task_plan=settled, plan_only=False) is False
    assert task_plan_blocks_conclusion(task_plan=None, plan_only=False) is False


def test_goal_reviewer_accepts_on_structured_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENSRE_REACT_GOAL_LLM_REVIEW_ENV, "1")
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(llm, "delete the cron", executed_tool_names=["shell_run"])
    assert goal.verify is not None
    assert goal.verify(_obs()) is True


def test_goal_reviewer_fails_open_on_free_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prose without JSON must accept (fail open) — never force extra ReAct work."""
    monkeypatch.setenv(OPENSRE_REACT_GOAL_LLM_REVIEW_ENV, "1")
    llm = _ScriptedLLM("NOT_REACHED — keep going")
    goal = build_goal_reviewer(llm, "delete the cron", executed_tool_names=["shell_run"])
    assert goal.verify is not None
    assert goal.verify(_obs()) is True


def test_goal_reviewer_skips_llm_by_default() -> None:
    llm = _ScriptedLLM('{"verdict": "NOT_REACHED"}')
    goal = build_goal_reviewer(llm, "delete the cron", executed_tool_names=["shell_run"])
    assert goal.verify is not None
    assert goal.verify(_obs()) is True
    assert llm.invokes == 0


def test_gather_reviewer_rejects_discovery_only_without_llm() -> None:
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    calls = [("list_posthog_tools", {})]
    goal = build_gather_goal_reviewer(llm, "how many Windows users?", executed_tool_calls=calls)
    assert goal.verify is not None
    assert goal.verify(_obs()) is False
    assert llm.invokes == 0


def test_structured_answer_goal_review_recovers_latest_non_qa_user_request() -> None:
    original = "For facebook/react, return merged PR count, median time-to-merge, and star gain."
    prior_answers = format_ask_user_answers(
        (AskUserQuestion(label="Window", title="Which window?", options=("7d", "30d")),),
        ("7d",),
    )
    current_answers = format_ask_user_answers(
        (AskUserQuestion(label="Zone", title="Which timezone?", options=("UTC", "Local")),),
        ("UTC",),
    )
    snapshot = TurnSnapshot(
        text=current_answers,
        conversation_messages=(
            ("user", original),
            ("assistant", "Which window?"),
            ("user", prior_answers),
            ("assistant", "Which timezone?"),
        ),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
    )

    assert _goal_review_user_request(current_answers, snapshot) == original


def test_ordinary_turn_goal_review_uses_current_message() -> None:
    snapshot = TurnSnapshot(
        text="Run the same analysis for vercel/next.js",
        conversation_messages=(("user", "Analyze facebook/react"),),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
    )

    assert _goal_review_user_request(snapshot.text, snapshot) == snapshot.text


def test_goal_reviewer_rejects_a_turn_that_blocked_a_step_without_asking_the_user() -> None:
    """A blocked step is resolved with the user, not skipped: the turn ends through a question."""
    # Arrange: the plan was worked this turn and a step was newly blocked.
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "inspect the repository",
        executed_tool_names=["update_plan"],
        blocked_needs_user=lambda: True,
    )
    assert goal.verify is not None and goal.nudge is not None

    # Act / Assert: rejected without LLM spend, and the nudge names the fix.
    assert goal.verify(_obs()) is False
    assert llm.invokes == 0
    assert "ask_user_choice" in goal.nudge(_obs())


def test_blocked_steps_await_the_user_until_a_question_is_queued() -> None:
    from types import SimpleNamespace

    from core.agent_harness.task_plan.conclusion import blocked_steps_await_the_user
    from core.agent_harness.task_plan.evidence import record_blocked_this_turn, reset_plan_evidence

    # Arrange
    session = SimpleNamespace(pending_user_choice=None)
    reset_plan_evidence(session)
    assert blocked_steps_await_the_user(session) is False

    # Act: a write blocks a step this turn.
    record_blocked_this_turn(session, ("Inspect repository",))

    # Assert: the user must be asked; once a question is queued the turn may end.
    assert blocked_steps_await_the_user(session) is True
    # The turn that carries their answer has consulted them already.
    assert blocked_steps_await_the_user(session, user_answered=True) is False
    session.pending_user_choice = object()
    assert blocked_steps_await_the_user(session) is False


def test_goal_reviewer_rejects_a_turn_that_stopped_after_a_failed_curl() -> None:
    """A nonzero shell/curl is an observation, not completion of the user's ask."""
    from core.agent_harness.turns.work_outcome import ExecutedToolOutcome

    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    outcomes = [
        ExecutedToolOutcome(
            name="shell_run",
            arguments={"command": "curl https://api.github.com/repos/facebook/react"},
            is_error=False,
            details={"ok": False, "exit_code": 1, "stderr": "Could not resolve host"},
        )
    ]
    goal = build_goal_reviewer(
        llm,
        "how many stars does facebook/react have",
        executed_tool_names=["shell_run"],
        executed_outcomes=outcomes,
    )
    assert goal.verify is not None and goal.nudge is not None
    assert goal.verify(_obs()) is False
    assert llm.invokes == 0
    assert "failed" in goal.nudge(_obs()).lower()
    # Keep rejecting until a later work tool succeeds — one failed curl is
    # not a budget that then lets the model stop.
    assert goal.verify(_obs()) is False


def test_goal_reviewer_accepts_after_a_failed_curl_is_retried_successfully() -> None:
    from core.agent_harness.turns.work_outcome import ExecutedToolOutcome

    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    outcomes = [
        ExecutedToolOutcome(
            name="shell_run",
            arguments={"command": "curl …"},
            is_error=False,
            details={"ok": False, "exit_code": 1},
        ),
        ExecutedToolOutcome(
            name="get_github_repository",
            arguments={"owner": "facebook", "repo": "react"},
            is_error=False,
            details={"ok": True, "stargazers_count": 42},
        ),
    ]
    goal = build_goal_reviewer(
        llm,
        "how many stars does facebook/react have",
        executed_tool_names=["shell_run", "get_github_repository"],
        executed_outcomes=outcomes,
    )
    assert goal.verify is not None
    assert goal.verify(_obs()) is True
    assert llm.invokes == 0


def test_goal_reviewer_accepts_a_classified_blocked_repair() -> None:
    """A repair tool that already classified the target may end and report it."""
    from core.agent_harness.turns.work_outcome import ExecutedToolOutcome

    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    outcomes = [
        ExecutedToolOutcome(
            name="fix_github_pr_ci",
            arguments={},
            is_error=False,
            details={
                "success": False,
                "error_kind": "repo_mismatch",
                "work_outcome": {"status": "blocked", "error_kind": "repo_mismatch"},
            },
        )
    ]
    goal = build_goal_reviewer(
        llm,
        "repair the failing checks",
        executed_tool_names=["fix_github_pr_ci"],
        executed_outcomes=outcomes,
    )
    assert goal.verify is not None
    assert goal.verify(_obs()) is True
    assert llm.invokes == 0


def test_goal_reviewer_lets_the_user_be_asked_after_a_failed_tool() -> None:
    """A closing question is how a real blocker is resolved; do not flail past it."""
    from core.agent_harness.turns.work_outcome import ExecutedToolOutcome

    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    outcomes = [
        ExecutedToolOutcome(
            name="shell_run",
            arguments={"command": "curl"},
            is_error=False,
            details={"ok": False, "exit_code": 1},
        )
    ]
    goal = build_goal_reviewer(
        llm,
        "how many stars",
        executed_tool_names=["shell_run"],
        executed_outcomes=outcomes,
    )
    assert goal.verify is not None
    assert goal.verify(_obs(text="GitHub returned 401. Do you have a token I can use?")) is True

    """Live: demo A was picked, the skill loaded, and the turn ended on "Next, I'll scan"."""
    # Arrange: the gate says the turn stalled on a skill load.
    llm = _ScriptedLLM('{"verdict": "GOAL_REACHED"}')
    goal = build_goal_reviewer(
        llm,
        "run demo A",
        executed_tool_names=["skill_view"],
        skill_load_only=lambda: True,
    )
    assert goal.verify is not None and goal.nudge is not None

    # Act / Assert: rejected once with the fix named, then accepted — one extra call at most.
    assert goal.verify(_obs()) is False
    assert "run its first step" in goal.nudge(_obs())
    assert goal.verify(_obs()) is True
    assert llm.invokes == 0


def test_a_demo_pick_stalls_only_when_the_chosen_skill_was_loaded_and_nothing_else_done() -> None:
    from types import SimpleNamespace

    from core.agent_harness.task_plan.conclusion import demo_pick_stalled_on_skill_load
    from core.agent_harness.task_plan.evidence import record_plan_evidence, reset_plan_evidence

    # Arrange: a skill body was loaded this turn.
    session = SimpleNamespace(pending_user_choice=None)
    reset_plan_evidence(session)
    record_plan_evidence(session, "skill_view", {"name": "analyzing-github-ci-performance"})

    # Act / Assert: only the onboarding menu's answer stalls; a question or a
    # hand-off between workflow skills does not.
    assert demo_pick_stalled_on_skill_load(session, user_answered=True, from_onboarding_menu=True)
    assert not demo_pick_stalled_on_skill_load(
        session, user_answered=False, from_onboarding_menu=True
    )
    assert not demo_pick_stalled_on_skill_load(
        session, user_answered=True, from_onboarding_menu=False
    )
    # A menu the skill queued, or any work, means the turn did something.
    session.pending_user_choice = object()
    assert not demo_pick_stalled_on_skill_load(
        session, user_answered=True, from_onboarding_menu=True
    )
    session.pending_user_choice = None
    record_plan_evidence(session, "scan_local_git_workspace", {})
    assert not demo_pick_stalled_on_skill_load(
        session, user_answered=True, from_onboarding_menu=True
    )
