"""Unit tests for the turn-wide assembly object ``TurnPlan``."""

from __future__ import annotations

from dataclasses import replace

import pytest

from config.constants.runtime_metadata import OPENSRE_WORKSPACE_REPO_ENV
from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
from core.agent_harness.turns.turn_plan import TurnPlan, build_turn_plan, preview_repositories
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
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
    monkeypatch.setenv(OPENSRE_WORKSPACE_REPO_ENV, "Tracer-Cloud/opensre")
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


def test_a_preview_targets_the_turn_s_repository_and_leaves_the_session_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENSRE_WORKSPACE_REPO_ENV, "Tracer-Cloud/opensre")
    session = Session()
    before = TurnSnapshot.from_session("", session, surface="interactive_shell")

    # Before the first resolve a preview cannot know (resolving could reach the network).
    assert preview_repositories(before, session) == before

    session.resolved_integrations_cache = {"github": {"connection_verified": True}}
    preview = preview_repositories(
        TurnSnapshot.from_session("", session, surface="interactive_shell"), session
    )

    assert preview.active_vcs_repositories == {"github": "Tracer-Cloud/opensre"}
    assert preview.resolved_integrations["github"]["repo"] == "opensre"
    assert session.active_vcs_repositories == {}
    assert session.vcs_repo_scopes == {}
    assert session.known_vcs_repo_scopes == {}
