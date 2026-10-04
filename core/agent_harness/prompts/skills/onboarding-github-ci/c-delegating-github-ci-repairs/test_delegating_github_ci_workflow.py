"""The shell delegates once and observes the same remote repair after target selection."""

from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, OPENSRE_MEMORY_DIR_ENV
from core.llm.types import AgentLLMResponse, ToolCall
from tests.core.agent.orchestration.action_execution_test_harness import (
    no_tool_response,
    tool_response,
)
from tests.utils.skill_workflow import BINDING, SkillWorkflow, batch

_SKILL = "delegating-github-ci-repairs"


def test_an_approved_demo_repository_is_the_target_without_another_question() -> None:
    body = Path(__file__).with_name("SKILL.md").read_text(encoding="utf-8")
    assert "Create a private demo repository?" in body
    assert "Do not call `ask_user_choice` for it." in body


_DEMO = "Use a disposable demo repository"
_REPORT = "Task repair-1 succeeded remotely: failing run 1, repair commit abc, passing run 2."
_REQUEST = {
    "prompt": "Use scheduling-github-ci-repairs on this gateway for the selected private demo.",
    "facts": {"demo": "true", "owner": "Tracer-Cloud"},
}
_OBSERVE = {
    "prompt": "Inspect and wait for hosted task repair-1 only.",
    "facts": {"task_id": "repair-1"},
}


def _call(call_id: str, name: str, args: dict[str, Any]) -> AgentLLMResponse:
    """One scripted tool call with its own id, so two plan writes can share a response."""
    return AgentLLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, input=args)],
        raw_content=None,
    )


def _plan(completed: int, active: int, *, known_target: bool) -> dict[str, Any]:
    steps = ["Prepare gateway", "Select target", "Delegate", "Verify repair", "Report", "Follow-up"]
    if known_target:
        steps.remove("Select target")
    plan: list[dict[str, Any]] = [
        {"step": step, "status": "completed" if index < completed else "pending"}
        for index, step in enumerate(steps)
    ]
    plan[active]["status"] = "in_progress"
    plan[steps.index("Verify repair")]["verifies"] = True
    plan[steps.index("Report")]["deliverable"] = True
    return {"plan": plan}


@pytest.mark.parametrize("known_target", [False, True])
def test_remote_target_preserves_handoff_and_report_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, known_target: bool
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    responses: list[Any] = [tool_response("skill_view", {"name": _SKILL})]
    if known_target:
        responses.append(
            batch(
                tool_response("update_plan", _plan(0, 0, known_target=True)),
                tool_response("check_hosted_gateway"),
            )
        )
    else:
        # Complete the gateway check beside the write that starts the repository
        # question, so that step is in_progress before its menu. A menu cannot
        # share a response with update_plan.
        responses.extend(
            [
                batch(
                    _call("plan-start", "update_plan", _plan(0, 0, known_target=False)),
                    tool_response("check_hosted_gateway"),
                    _call("plan-select", "update_plan", _plan(1, 1, known_target=False)),
                ),
                tool_response(
                    "ask_user_choice",
                    {
                        "title": "Remote Repair Target",
                        "options": [_DEMO, "Use an existing pull request"],
                    },
                ),
            ]
        )
    delegate_step = 1 if known_target else 2
    responses.extend(
        [
            batch(
                tool_response(
                    "update_plan", _plan(delegate_step, delegate_step, known_target=known_target)
                ),
                tool_response("ask_hosted_gateway", _REQUEST),
            ),
            # The observe call sits between the two writes: the first starts
            # verification, the return is the evidence that completes it, and
            # the second leaves the report step in_progress for the text reply.
            batch(
                _call(
                    "plan-verify",
                    "update_plan",
                    _plan(delegate_step + 1, delegate_step + 1, known_target=known_target),
                ),
                tool_response("ask_hosted_gateway", _OBSERVE),
                _call(
                    "plan-report",
                    "update_plan",
                    _plan(delegate_step + 2, delegate_step + 2, known_target=known_target),
                ),
            ),
            no_tool_response(_REPORT),
            tool_response(
                "ask_user_choice",
                {
                    "title": "What next?",
                    "options": ["Configure Slack or Telegram", "Exit to interactive shell"],
                },
            ),
        ]
    )
    workflow = SkillWorkflow(Path(__file__).with_name("SKILL.md"), responses)
    agent = workflow.build(
        [
            workflow.external("check_hosted_gateway", [{"success": True, "state": "running"}]),
            workflow.external(
                "ask_hosted_gateway",
                [
                    {
                        "success": True,
                        "state": "done",
                        "prompt_id": "p_start",
                        "response_text": "Task repair-1 is running; deadline unchanged.",
                    },
                    {
                        "success": True,
                        "state": "done",
                        "prompt_id": "p_status",
                        "response_text": _REPORT,
                    },
                ],
            ),
        ]
    )

    agent.handle(
        "Run the private demo remotely" if known_target else "Run CI/CD repairs remotely", BINDING
    )

    if not known_target:
        assert workflow.calls == [("check_hosted_gateway", {})]
        answer = workflow.answer("Remote Repair Target", _DEMO)
        agent.handle(answer, BINDING)

    assert workflow.calls == [
        ("check_hosted_gateway", {}),
        ("ask_hosted_gateway", _REQUEST),
        ("ask_hosted_gateway", _OBSERVE),
    ]
    assert workflow.output.streamed.count(_REPORT) == 1
    assert workflow.session.pending_user_choice is not None
    assert workflow.session.pending_user_choice.title == "What next?"
    workflow.assert_finished()


def test_missing_gateway_skill_reports_blocker_before_recovery_menu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    report = (
        "Gateway version mismatch: scheduling-github-ci-repairs is unavailable; no task exists."
    )
    blocked = _plan(1, 3, known_target=True)
    blocked["explanation"] = report
    blocked["plan"][1]["status"] = "blocked"
    blocked["plan"][2]["status"] = "blocked"
    workflow = SkillWorkflow(
        Path(__file__).with_name("SKILL.md"),
        [
            tool_response("skill_view", {"name": _SKILL}),
            batch(
                tool_response("update_plan", _plan(0, 0, known_target=True)),
                tool_response("check_hosted_gateway"),
            ),
            batch(
                tool_response("update_plan", _plan(1, 1, known_target=True)),
                tool_response("ask_hosted_gateway", _REQUEST),
            ),
            tool_response("update_plan", blocked),
            no_tool_response(report),
            tool_response(
                "ask_user_choice",
                {
                    "title": "Remote Demo Blocked",
                    "options": ["Retry after gateway update", "Leave the demo blocked"],
                },
            ),
        ],
    )
    agent = workflow.build(
        [
            workflow.external("check_hosted_gateway", [{"success": True, "state": "running"}]),
            workflow.external(
                "ask_hosted_gateway",
                [
                    {
                        "success": True,
                        "state": "done",
                        "prompt_id": "p_blocked",
                        "response_text": report,
                    }
                ],
            ),
        ]
    )

    agent.handle("Run the private demo remotely", BINDING)

    assert workflow.calls == [("check_hosted_gateway", {}), ("ask_hosted_gateway", _REQUEST)]
    assert workflow.output.streamed.count(report) == 1
    assert workflow.session.pending_user_choice is not None
    assert workflow.session.pending_user_choice.title == "Remote Demo Blocked"
    workflow.assert_finished()
