"""The master skill owns the menu and refers to four independently loadable children."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import core.agent_harness.prompts.skills.loader as loader
from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.prompts.action import build_action_system_prompt
from core.agent_harness.prompts.action.assemble import build_action_system_prompt_envelope
from core.agent_harness.prompts.getting_started import (
    GETTING_STARTED_CUSTOM,
    GETTING_STARTED_OPTIONS,
    getting_started_skills,
    load_getting_started_block,
)
from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def test_master_menu_matches_four_unique_children_and_preserves_specialists() -> None:
    loader.clear_skills_caches()
    children = getting_started_skills()
    assert [s.name for s in children] == [
        "cicd-analytics-demo",
        "cicd-reliability-agent",
        "remote-managed-service",
        "slack-handoff",
    ]
    assert [s.demo_order for s in children] == [1, 2, 3, 4]
    assert GETTING_STARTED_OPTIONS == (
        "Explore a repo and analyze its CI/CD performance (recommended)",
        "Set up an agent that improves CI/CD reliability over time",
        "Run CI/CD improvements with a managed service (coming soon)",
        "Connect OpenSRE to Slack and hand off DevOps chores for your team",
    )
    master = loader.load_skill_body(ONBOARDING_SKILL_NAME)
    master_skill = next(s for s in loader.list_action_skills() if s.name == ONBOARDING_SKILL_NAME)
    # The menu is data the host runs on entry, not prose the model replays; its
    # options are the children's own labels so the two cannot drift apart.
    assert [call.tool for call in master_skill.pre_execute] == ["ask_user_choice"]
    menu = master_skill.pre_execute[0].args
    assert menu["title"] == "Which demo would you like me to run? (Esc to skip)"
    assert menu["note"]
    assert tuple(menu["options"]) == GETTING_STARTED_OPTIONS
    assert "Call `ask_user_choice`" not in master
    for skill in children:
        assert f"`{skill.name}`" in master
        assert skill.path.parent.parent.name == "onboarding_cicd_fix"
        assert loader.load_skill_body(skill.name)
    assert GETTING_STARTED_CUSTOM in master
    assert "not implemented yet" in loader.load_skill_body("remote-managed-service")
    discovered = [
        loader._load_action_skill(path) for path in loader._iter_skill_paths(loader.skills_dir())
    ]
    names = [skill.name for skill in discovered if skill is not None]
    assert len(names) == len(set(names))
    assert ONBOARDING_SKILL_NAME in loader.load_skills_index()
    assert loader.load_skill_body("github-ci-fix")


def test_capability_and_demo_prompts_load_master_instead_of_defining_another_menu() -> None:
    snapshot = TurnSnapshot(
        text="What can you do?",
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
        prompt_surface="interactive_shell",
        interactive_choice_available=True,
    )
    prompt = " ".join(build_action_system_prompt(snapshot).split())
    assert f'call skill_view(name="{ONBOARDING_SKILL_NAME}")' in prompt
    assert "Do not ask a separate onboarding question before loading it" in prompt
    assert "are NOT a skill_view match" not in prompt
    assert "Which demo would you like me to run?" not in load_getting_started_block()
    assert "ask_user_choice menu: available" in prompt


def test_answer_keeps_skill_in_ephemeral_context_after_history_is_lost() -> None:
    question = AskUserQuestion(
        label="", title="Which repository?", options=("acme/one", "acme/two")
    )
    snapshot = TurnSnapshot(
        text=format_ask_user_answers((question,), ("acme/one",)),
        conversation_messages=(),
        configured_integrations=(),
        configured_integrations_known=True,
        reasoning_effort=None,
        active_skill="cicd-analytics-demo",
    )
    answer = build_action_system_prompt_envelope(snapshot)
    fresh = build_action_system_prompt_envelope(replace(snapshot, text="Explain this deployment"))
    body = loader.load_skill_body("cicd-analytics-demo")
    assert body in answer.render_ephemeral()
    assert body not in fresh.render()
    assert answer.render_cached() == fresh.render_cached()


def test_loader_discovers_nested_and_legacy_packages_without_hidden_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for relative, name in [
        ("master/SKILL.md", "master"),
        ("master/child/SKILL.md", "child"),
        ("master/legacy/legacy.md", "legacy"),
        ("master/.hidden/SKILL.md", "hidden"),
        (".private/child/SKILL.md", "private"),
        ("flat.md", "flat"),
    ]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {name}\ndescription: {name} recipe\n---\nBody of {name}.")
    monkeypatch.setattr(loader, "skills_dir", lambda: tmp_path)
    loader.clear_skills_caches()
    try:
        assert [s.name for s in loader.list_action_skills()] == [
            "master",
            "child",
            "legacy",
            "flat",
        ]
        assert loader.load_skill_body("child") == "Body of child."
        assert "master" in loader.load_skills_index()
    finally:
        loader.clear_skills_caches()


def test_pre_execute_keeps_well_formed_calls_and_drops_the_rest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "hooked.md").write_text(
        "---\n"
        "name: hooked\n"
        "description: hooked recipe\n"
        "pre_execute:\n"
        "  - tool: ask_user_choice\n"
        "    args: {title: Pick, options: [a, b]}\n"
        "  - tool: shell_run\n"
        "  - not a mapping\n"
        "  - args: {title: no tool}\n"
        "---\n"
        "Body."
    )
    (tmp_path / "scalar.md").write_text(
        "---\nname: scalar\ndescription: scalar recipe\npre_execute: ask_user_choice\n---\nBody."
    )
    monkeypatch.setattr(loader, "skills_dir", lambda: tmp_path)
    loader.clear_skills_caches()
    try:
        by_name = {skill.name: skill for skill in loader.list_action_skills()}
        assert [(c.tool, dict(c.args)) for c in by_name["hooked"].pre_execute] == [
            ("ask_user_choice", {"title": "Pick", "options": ["a", "b"]})
        ]
        assert by_name["scalar"].pre_execute == ()
    finally:
        loader.clear_skills_caches()
