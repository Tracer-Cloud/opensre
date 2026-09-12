"""The master skill owns the menu and refers to three independently loadable children."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import core.agent_harness.prompts.skills.loader as loader
from config.constants.skills import ONBOARDING_SKILL_NAME, SKIP_DEMO_OPTION
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
from tests.utils.skill_cards import skill_card


def test_child_directories_are_letter_prefixed_skill_names() -> None:
    """``<letter>-<name>/`` keeps disk order, menu order, and ``name`` from drifting apart."""
    loader.clear_skills_caches()
    for skill in getting_started_skills():
        directory = skill.path.parent.name
        letter, _, suffix = directory.partition("-")
        assert len(letter) == 1 and letter.islower(), directory
        assert skill.demo_order == ord(letter) - ord("a") + 1, directory
        assert suffix == skill.name, directory


def test_master_menu_matches_three_unique_children_and_preserves_specialists() -> None:
    loader.clear_skills_caches()
    children = getting_started_skills()
    assert [s.name for s in children] == [
        "analyzing-github-ci-performance",
        "scheduling-github-ci-fixes",
        "connecting-slack",
    ]
    assert [s.demo_order for s in children] == [1, 2, 3]
    assert GETTING_STARTED_OPTIONS == (
        "Explore a repo and analyze its CI/CD performance (recommended)",
        "Set up an agent that improves CI/CD reliability over time",
        "Connect OpenSRE to Slack and hand off DevOps chores for your team",
    )
    master = loader.load_skill_body(ONBOARDING_SKILL_NAME)
    master_skill = next(s for s in loader.list_action_skills() if s.name == ONBOARDING_SKILL_NAME)
    # The menu is data the host runs on entry, not prose the model replays; its
    # options are the children's own labels so the two cannot drift apart.
    assert [call.tool for call in master_skill.pre_execute] == ["ask_user_choice"]
    menu = master_skill.pre_execute[0].args
    assert menu["title"] == "Which demo would you like me to run?"
    assert "note" not in menu
    assert tuple(menu["options"]) == (*GETTING_STARTED_OPTIONS, SKIP_DEMO_OPTION)
    assert "Call `ask_user_choice`" not in master
    for skill in children:
        assert f'skill_view(name="{skill.name}")' in master
        assert skill.path.parent.parent.name == ONBOARDING_SKILL_NAME
        assert loader.load_skill_body(skill.name)
    analytics = next(s for s in children if s.name == "analyzing-github-ci-performance")
    # The analytics card runs its menus from the plan the model follows, not
    # from host hooks, and keeps the full tool catalog.
    assert analytics.pre_execute == ()
    body = loader.load_skill_body("analyzing-github-ci-performance")
    assert "`Which repository should I analyze?`" in body
    assert "`What would you like to do next?`" in body
    for option in ("- Schedule local loops", "- Slack setup", "- Finish"):
        assert option in body
    # Each next-step branch hands off to its sibling skill instead of inlining it.
    assert 'skill_view(name="scheduling-github-ci-fixes")' in body
    assert 'skill_view(name="connecting-slack")' in body
    # The comparison is the tool's job, not a flag the model can forget; the
    # report shape is the skill's, so no flag on the tool picks one either.
    assert "include_benchmarks" not in body
    assert "compact=" not in body
    assert "Compare these numbers" not in body
    assert "Output its `headline`" not in body
    assert "same-day snapshot" not in body
    fix_loop = loader.load_skill_body("scheduling-github-ci-fixes")
    # The fix loop repairs red pull requests; it is not the analytics report
    # loop, so it never reaches for the analytics or report-scheduling tools.
    assert "fix_github_pr_ci" in fix_loop
    assert "summarize_github_pr_status" in fix_loop
    assert "analyze_github_ci_reliability" not in fix_loop
    assert "schedule_ci_reliability_loop" not in fix_loop
    # Repository selection remains part of the child workflow.
    assert "ask_user_choice" in fix_loop
    assert menu["allow_custom"] is False
    assert GETTING_STARTED_CUSTOM not in master
    assert loader.load_skill_body("delegating-github-ci-fixes") == ""
    discovered = [
        loader.validate_skill_file(path) for path in loader._iter_skill_paths(loader.skills_dir())
    ]
    names = [skill.name for skill in discovered if skill is not None]
    assert len(names) == len(set(names))
    assert ONBOARDING_SKILL_NAME in loader.load_skills_index()
    assert loader.load_skill_body("fixing-github-ci")


def test_multi_step_skills_track_progress_with_update_plan_not_step_headers() -> None:
    """Progress lives in update_plan (survives ask_user_choice turn boundaries).

    The old hand-emitted "### [n/N]" header protocol must not creep back: it
    contradicted the base prompt's header rules and vanished from context at
    every menu answer.
    """
    loader.clear_skills_caches()
    multi_step = (
        "analyzing-github-ci-performance",
        "scheduling-github-ci-fixes",
        "connecting-slack",
        "delivering-morning-briefings",
    )
    for name in multi_step:
        body = loader.load_skill_body(name)
        assert "update_plan" in body, name
        assert "### [" not in body, name
    for name in (ONBOARDING_SKILL_NAME,):
        assert "### [" not in loader.load_skill_body(name), name


def test_capability_answers_and_direct_requests_do_not_require_onboarding() -> None:
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
    assert "answer first and offer /demo" in prompt
    assert "An onboarding router delegates the live plan to its child" in prompt
    assert "For an ambiguous CI request, clarify the desired outcome once" in prompt
    assert "load that specialist directly and carry the original request forward" in prompt
    assert "an explicit demo or onboarding request that needs path selection" in prompt
    assert "stop onboarding without a replacement text menu" in prompt
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
        active_skill="analyzing-github-ci-performance",
    )
    answer = build_action_system_prompt_envelope(snapshot)
    fresh = build_action_system_prompt_envelope(replace(snapshot, text="Explain this deployment"))
    body = loader.load_skill_body("analyzing-github-ci-performance")
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
        path.write_text(skill_card(name, f"Body of {name}."))
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


def test_includes_append_shared_markdown_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "common").mkdir()
    (tmp_path / "common/rule.md").write_text("# Shared\n\nDo it once.")
    (tmp_path / "demo.md").write_text(
        skill_card("demo", "Body of demo.", includes=["common/rule.md", "common/rule.md"])
    )
    monkeypatch.setattr(loader, "skills_dir", lambda: tmp_path)
    loader.clear_skills_caches()
    try:
        body = loader.load_skill_body("demo")
        assert body.startswith("Body of demo.")
        assert body.count("Do it once.") == 1
        assert "SHARED RULES from" in body
    finally:
        loader.clear_skills_caches()


def test_onboarding_children_load_shared_rules_once() -> None:
    loader.clear_skills_caches()
    by_name = {s.name: s for s in loader.list_action_skills()}
    reliability = loader.load_skill_body("scheduling-github-ci-fixes")
    analytics = loader.load_skill_body("analyzing-github-ci-performance")
    analytics_card = by_name["analyzing-github-ci-performance"].path.read_text(encoding="utf-8")
    # Shared rules resolve from the skills-tree ``common/`` folder and are
    # appended exactly once, never copied into the card body.
    assert by_name["scheduling-github-ci-fixes"].includes == ("common/ask_once.md",)
    assert reliability.count("Ask each question once.") == 1
    assert "Ask each question once." not in by_name["scheduling-github-ci-fixes"].path.read_text(
        encoding="utf-8"
    )
    # The analytics card lists no shared rules; its plan carries its own wording.
    assert by_name["analyzing-github-ci-performance"].includes == ()
    assert "SHARED RULES from" not in analytics
    assert "## Progress updates" not in analytics_card
