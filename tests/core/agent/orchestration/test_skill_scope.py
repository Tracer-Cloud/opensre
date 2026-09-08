"""An answer turn inside a skill offers only the skill's tools; a new request clears the scope."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
from core.agent_harness.turns.skill_scope import scope_tools_to_active_skill


def _tool(name: str) -> Any:
    return type("Tool", (), {"name": name})()


def _session(tools: tuple[str, ...]) -> Any:
    return type(
        "Session", (), {"active_skill": "cicd-analytics-demo", "active_skill_tools": tools}
    )()


_ANSWER = format_ask_user_answers(
    (AskUserQuestion(label="", title="Which repository should I analyze?", options=("a",)),),
    ("Tracer-Cloud/opensre (757 commits, CI configured)",),
)
_ALL = [
    _tool(n)
    for n in (
        "scan_local_git_workspace",
        "analyze_github_ci_reliability",
        "cli_exec",
        "github_cli",
        "ask_user_choice",
        "update_plan",
    )
]


def test_answer_turn_inside_a_skill_drops_tools_the_skill_did_not_declare() -> None:
    session = _session(
        ("scan_local_git_workspace", "analyze_github_ci_reliability", "ask_user_choice")
    )

    scoped = scope_tools_to_active_skill(_ALL, session, _ANSWER)

    assert [t.name for t in scoped] == [
        "scan_local_git_workspace",
        "analyze_github_ci_reliability",
        "ask_user_choice",
        "update_plan",
    ]
    assert session.active_skill == "cicd-analytics-demo"


def test_a_genuine_user_turn_clears_the_scope_and_keeps_every_tool() -> None:
    session = _session(("scan_local_git_workspace",))

    scoped = scope_tools_to_active_skill(_ALL, session, "check the health of my integrations")

    assert scoped == _ALL
    assert session.active_skill is None
    assert session.active_skill_tools == ()


def test_a_skill_without_declared_tools_leaves_the_answer_turn_unscoped() -> None:
    session = _session(())

    assert scope_tools_to_active_skill(_ALL, session, _ANSWER) == _ALL
