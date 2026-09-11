"""Unit tests for the turn-wide assembly object ``TurnPlan``."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.agent_harness.turns.turn_plan as turn_plan_module
from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
from core.agent_harness.turns.turn_plan import (
    TurnPlan,
    build_turn_plan,
    refresh_repository_scopes_after_directory_change,
    working_directory_scope_refresh_hooks,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from core.tool.execution import ToolExecutionResult
from surfaces.interactive_shell.session import Session


def _snapshot(text: str = "q", *, resolved: dict | None = None) -> TurnSnapshot:
    snapshot = TurnSnapshot.from_session(text, Session(), surface="interactive_shell")
    if resolved is not None:
        snapshot = replace(snapshot, resolved_integrations=resolved)
    return snapshot


def test_build_turn_plan_composes_the_snapshot() -> None:
    snapshot = _snapshot(
        "why did example/repository fail?",
        resolved={
            "github": {
                "configured": True,
                "owner": "example",
                "repo": "repository",
            }
        },
    )

    plan = build_turn_plan(snapshot, Session())

    assert isinstance(plan, TurnPlan)
    assert plan.text == "why did example/repository fail?"


def test_turn_plan_exposes_resolved_integrations_from_snapshot() -> None:
    resolved = {
        "github": {
            "configured": True,
            "owner": "example",
            "repo": "repository",
        }
    }
    snapshot = _snapshot("check example/repository", resolved=resolved)

    plan = build_turn_plan(snapshot, Session())

    # The plan is the single source: it reads the snapshot's resolved view, not a copy.
    assert plan.resolved_integrations == resolved
    assert plan.resolved_integrations is plan.snapshot.resolved_integrations


def test_build_turn_plan_resolves_integrations_when_snapshot_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_turn_plan owns the resolve step, running it when the snapshot is empty."""
    resolved = {"datadog": {"configured": True}}
    monkeypatch.setattr(
        "core.agent_harness.turns.turn_plan.resolve_and_cache_integrations",
        lambda _session: resolved,
    )

    plan = build_turn_plan(_snapshot("check example/repository"), Session())

    assert plan.resolved_integrations == resolved


def test_build_turn_plan_resolves_when_snapshot_is_metadata_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway chat metadata alone must not skip the real resolve."""
    resolved = {"slack": {"bot_token": "xoxb-test"}}
    monkeypatch.setattr(
        "core.agent_harness.turns.turn_plan.resolve_and_cache_integrations",
        lambda _session: resolved,
    )

    plan = build_turn_plan(
        _snapshot("check example/repository", resolved={"_gateway_chat_id": "C0123ABCD"}),
        Session(),
    )

    assert plan.resolved_integrations == resolved


def test_ask_user_answer_resolves_explicit_repo_from_history_without_mutating_base() -> None:
    session = Session()
    base = {
        "github": {
            "connection_verified": True,
            "owner": "Tracer-Cloud",
            "repo": "opensre",
        }
    }
    session.resolved_integrations_cache = base
    session.cli_agent_messages = [
        (
            "user",
            "For facebook/react, return merged PR count, median time-to-merge, and star gain.",
        ),
        ("assistant", "Which date window should I use?"),
    ]
    answers = format_ask_user_answers(
        (AskUserQuestion(label="Window", title="Which date window?", options=("7d", "30d")),),
        ("7d",),
    )

    plan = build_turn_plan(
        TurnSnapshot.from_session(answers, session, surface="interactive_shell"),
        session,
    )

    assert plan.resolved_integrations["github"]["owner"] == "facebook"
    assert plan.resolved_integrations["github"]["repo"] == "react"
    assert session.vcs_repo_scopes["github"] == ("facebook", "react")
    assert session.resolved_integrations_cache == base
    assert session.resolved_integrations_cache["github"]["owner"] == "Tracer-Cloud"


def test_repo_scope_is_sticky_and_current_message_can_override_it() -> None:
    session = Session()
    session.resolved_integrations_cache = {"github": {"connection_verified": True}}
    session.vcs_repo_scopes = {"github": ("facebook", "react")}

    follow_up = build_turn_plan(
        TurnSnapshot.from_session(
            "Now compare that with the prior week",
            session,
            surface="interactive_shell",
        ),
        session,
    )
    override = build_turn_plan(
        TurnSnapshot.from_session(
            "Run the same analysis for vercel/next.js",
            session,
            surface="interactive_shell",
        ),
        session,
    )

    assert follow_up.resolved_integrations["github"]["owner"] == "facebook"
    assert follow_up.resolved_integrations["github"]["repo"] == "react"
    assert override.resolved_integrations["github"]["owner"] == "vercel"
    assert override.resolved_integrations["github"]["repo"] == "next.js"
    assert session.vcs_repo_scopes["github"] == ("vercel", "next.js")


def test_repo_scope_switch_keeps_all_repository_contexts_in_session_memory() -> None:
    session = Session()
    session.resolved_integrations_cache = {"github": {"connection_verified": True}}

    first = build_turn_plan(
        TurnSnapshot.from_session(
            "Inspect facebook/react",
            session,
            surface="interactive_shell",
        ),
        session,
    )
    second = build_turn_plan(
        TurnSnapshot.from_session(
            "Now inspect vercel/next.js",
            session,
            surface="interactive_shell",
        ),
        session,
    )

    assert first.snapshot.active_vcs_repositories == {"github": "facebook/react"}
    assert second.snapshot.active_vcs_repositories == {"github": "vercel/next.js"}
    assert second.snapshot.known_vcs_repositories == {
        "github": ("facebook/react", "vercel/next.js")
    }
    assert session.known_vcs_repo_scopes["github"] == {
        "facebook/react": ("facebook", "react"),
        "vercel/next.js": ("vercel", "next.js"),
    }
    assert session.vcs_repo_scopes["github"] == ("vercel", "next.js")


def test_one_turn_remembers_all_named_repositories_and_activates_last() -> None:
    session = Session()
    session.resolved_integrations_cache = {"github": {"connection_verified": True}}

    plan = build_turn_plan(
        TurnSnapshot.from_session(
            "Compare facebook/react with vercel/next.js",
            session,
            surface="interactive_shell",
        ),
        session,
    )

    assert plan.snapshot.active_vcs_repositories == {"github": "vercel/next.js"}
    assert plan.snapshot.known_vcs_repositories == {"github": ("facebook/react", "vercel/next.js")}


def test_repo_scope_uses_workspace_only_without_explicit_or_sticky_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_WORKSPACE_REPO", "Tracer-Cloud/opensre")
    session = Session()
    session.resolved_integrations_cache = {"github": {"connection_verified": True}}

    plan = build_turn_plan(
        TurnSnapshot.from_session(
            "How many stars did this repo gain?",
            session,
            surface="interactive_shell",
        ),
        session,
    )

    assert plan.resolved_integrations["github"]["owner"] == "Tracer-Cloud"
    assert plan.resolved_integrations["github"]["repo"] == "opensre"
    assert session.vcs_repo_scopes["github"][:2] == ("Tracer-Cloud", "opensre")


def test_directory_change_refreshes_same_turn_repository_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for path, remote in (
        (repo_a, "https://github.com/example/repo-a.git"),
        (repo_b, "https://github.com/example/repo-b.git"),
    ):
        path.mkdir()
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", remote],
            check=True,
        )

    session = Session(working_directory=str(repo_a))
    session.resolved_integrations_cache = {"github": {"connection_verified": True}}
    initial = build_turn_plan(
        TurnSnapshot.from_session("check this repository", session, surface="interactive_shell"),
        session,
    )
    assert initial.resolved_integrations["github"]["repo"] == "repo-a"

    refreshed = refresh_repository_scopes_after_directory_change(
        resolved=initial.resolved_integrations,
        session=session,
        message="change directory and check this repository",
        cwd=str(repo_b),
    )

    assert refreshed["github"]["owner"] == "example"
    assert refreshed["github"]["repo"] == "repo-b"
    assert session.active_vcs_repositories == {"github": "example/repo-b"}


def test_directory_change_hook_updates_running_execution_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    running = {"github": {"configured": True, "repo": "repo-a"}}

    def _refresh(**kwargs: object) -> dict[str, object]:
        assert kwargs["resolved"] is running
        assert kwargs["cwd"] == "/workspace/repo-b"
        return {"github": {"configured": True, "repo": "repo-b"}}

    monkeypatch.setattr(
        turn_plan_module,
        "refresh_repository_scopes_after_directory_change",
        _refresh,
    )
    hooks = working_directory_scope_refresh_hooks(
        session=session,
        message="change directory and inspect this repository",
    )
    request = SimpleNamespace(
        tool_call=SimpleNamespace(name="set_working_directory"),
        resolved_integrations=running,
    )
    assert hooks.after_tool_call is not None

    hooks.after_tool_call(
        request,
        ToolExecutionResult(
            content="ok",
            details={"ok": True, "working_directory": "/workspace/repo-b"},
        ),
    )

    assert running["github"]["repo"] == "repo-b"
