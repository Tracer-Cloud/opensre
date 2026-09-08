"""Unit tests for shell action-agent prompt context."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_harness.prompts import (
    build_action_system_prompt,
    connected_integrations_block,
    prior_action_facts_block,
    recent_conversation_block,
    repository_context_block,
)
from core.agent_harness.prompts.memory.conversation import NO_HISTORY_PLACEHOLDER
from core.agent_harness.prompts.skills.loader import (
    SKILLS_HEADER,
    list_action_skills,
    load_skill_body,
    load_skills_block,
    load_skills_index,
    skills_dir,
)
from core.agent_harness.prompts.skills.loader import (
    load_skills_block as cached_load_skills_block,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def _skill_instruction_text(name: str) -> str:
    return " ".join(load_skill_body(name).replace("`", "").lower().split())


def _ctx(
    *,
    messages: list[tuple[str, str]] | None = None,
    integrations: tuple[str, ...] = (),
    integrations_known: bool = False,
    active_repositories: dict[str, str] | None = None,
    known_repositories: dict[str, tuple[str, ...]] | None = None,
) -> TurnSnapshot:
    return TurnSnapshot(
        text="",
        conversation_messages=tuple(messages or []),
        configured_integrations=integrations,
        configured_integrations_known=integrations_known,
        reasoning_effort=None,
        active_vcs_repositories=active_repositories or {},
        known_vcs_repositories=known_repositories or {},
    )


def test_recent_conversation_block_contains_history_lines() -> None:
    ctx = _ctx(
        messages=[
            ("user", "how can I remove github integration"),
            ("assistant", "Use /integrations remove github or /integrations list."),
            ("user", "yes schedule it"),
            ("assistant", "Queued via /cron."),
        ]
    )
    block = recent_conversation_block(ctx)
    assert "RECENT CONVERSATION" in block
    assert "newest first" in block
    # Newest turn leads so head-preserving truncation keeps follow-up context.
    assert block.index("yes schedule it") < block.index("how can I remove github")
    assert "Assistant: Use /integrations remove github or /integrations list." in block


def test_recent_conversation_block_placeholder_without_history() -> None:
    assert NO_HISTORY_PLACEHOLDER in recent_conversation_block(_ctx())


def test_prior_action_facts_block_surfaces_telegram_followup_values() -> None:
    ctx = _ctx(
        messages=[
            ("user", "Can you send the weather of both hawaii and antartica to slack?"),
            (
                "assistant",
                "Hawaii: +28C\n"
                "Antarctica: -24C\n"
                'slack_send_message input: {"message": "Hawaii: +28C\\nAntarctica: -24C"}\n'
                'slack_send_message result: {"sent": true}',
            ),
            ("user", "Write it in a nicer message and compare to London"),
            ("assistant", "London: +22C"),
        ]
    )

    block = prior_action_facts_block(ctx)
    assert "PRIOR ACTION FACTS" in block
    assert "Hawaii: +28C" in block
    assert "Antarctica: -24C" in block
    assert "London: +22C" in block
    assert "slack_send_message input" in block


def test_system_prompt_slack_fragment_documents_roster_followup() -> None:
    # Slack-specific "Want me to" roster follow-up now lives in
    # integrations.slack.action_prompt and is appended to the composed action
    # prompt via the harness-ports fragment registry, not hardcoded in core.
    prompt = build_action_system_prompt(_ctx()).lower()
    assert "want me to: offering more slack roster" in prompt
    assert "slack_list_team_members" in prompt


def test_system_prompt_routes_slack_teammate_reads_to_action_tools() -> None:
    # Vendor recipe now lives in integrations.slack.action_prompt and is
    # appended to the composed action prompt via the harness-ports fragment
    # registry (see integrations/harness_adapters.py), not hardcoded in core.
    prompt = build_action_system_prompt(_ctx()).lower()
    compact = prompt.replace(" ", "")
    assert "slack teammate requests use slack tools" in prompt
    assert 'slack_read_messages(channel="#opensre-slack-testing"' in compact
    assert "roster / people questions ignore channel_id" in prompt
    assert "slack_list_team_members only" in prompt
    assert "never slack_read_messages" in prompt


def test_system_prompt_routes_github_cli_to_action_tools() -> None:
    # Vendor recipe now lives in integrations.github.action_prompt and is
    # appended to the composed action prompt via the harness-ports fragment
    # registry (see integrations/harness_adapters.py), not hardcoded in core.
    prompt = build_action_system_prompt(_ctx()).lower()
    assert "github cli requests use github tools" in prompt
    assert "call github_cli directly" in prompt
    assert "from this info create an issue on github" in prompt
    assert "exception: github issue/pr/repo" in prompt
    assert "get_github_star_history" in prompt
    assert "day-by-day stars" in prompt


def test_system_prompt_slack_fragment_documents_invented_command_example() -> None:
    # The Slack-specific invented-delivery-command example now lives in
    # integrations.slack.action_prompt, appended via the harness-ports
    # fragment registry, not hardcoded in core.
    prompt = build_action_system_prompt(_ctx()).lower()
    compact_prompt = " ".join(prompt.split())
    assert "`/messaging send slack …` is not a real command" in compact_prompt


def test_morning_report_skill_closes_with_schedule_offer() -> None:
    """A run-once morning report without an offer cannot drive repeat usage."""
    load_skills_block.cache_clear()
    body = _skill_instruction_text("morning-report")
    assert "propose_scheduled_delivery" in body
    assert "recurring_skill" in body
    assert "morning-report" in body
    assert 'cron="0 8 * * 1-5"' in body or "cron='0 8 * * 1-5'" in body
    assert "do not call /cron yet" in body
    assert "do not start an investigation" in body
    # Intermediate curls must be quiet so the user does not see weather/news
    # once as $ stdout and again in the composed OpenSRE briefing.
    assert "quiet=true" in body
    assert "briefing_text" in body


def test_connected_integrations_block_renders_state() -> None:
    assert "unknown" in connected_integrations_block(_ctx())

    none_block = connected_integrations_block(_ctx(integrations=(), integrations_known=True))
    assert "none" in none_block
    assert "use available chat tools" in none_block.lower()

    listed = connected_integrations_block(
        _ctx(
            integrations=("sentry", "github", "posthog_mcp"),
            integrations_known=True,
        )
    )
    assert "github, posthog_mcp, sentry" in listed
    # Cause/why questions still route to chat tools, not a special pipeline.
    assert "use available chat tools" in listed.lower()


def test_repository_context_renders_one_active_and_multiple_remembered_repos() -> None:
    block = repository_context_block(
        _ctx(
            active_repositories={"github": "vercel/next.js"},
            known_repositories={
                "github": ("Tracer-Cloud/opensre", "vercel/next.js"),
            },
        )
    )

    assert "active=vercel/next.js" in block
    assert "remembered=Tracer-Cloud/opensre, vercel/next.js" in block
    assert "without deleting the others" in block
    assert repository_context_block(_ctx()) == ""


def test_skills_loader_bundles_architecture_audit_skill() -> None:
    cached_load_skills_block.cache_clear()
    skill_dir = skills_dir() / "architecture_audit"
    skill = skill_dir / "SKILL.md"
    template = skill_dir / "architecture_audit_report.md"
    assert skill.is_file()
    assert template.is_file()

    index = load_skills_index()
    assert "architecture-audit" in index
    assert "SKILLS INDEX" in index
    # Fat body stays out of the thin harness index.
    assert "architecture_clone_repo" not in index

    body = load_skill_body("architecture-audit")
    assert "summarize this repo's architecture" in body
    assert "architecture_clone_repo" in body
    assert "scan_architecture_imports" not in body
    assert "scan_module_placement" not in body
    assert "architecture_cleanup_repo" in body
    assert "architecture_save_observations" in body
    assert "shell_run" in body
    assert "Never end the turn with shell_run" in body
    report_path = (
        "core/agent_harness/prompts/skills/architecture_audit/architecture_audit_report.md"
    )
    assert f"REPORT TEMPLATE from `{report_path}`" in body
    assert "### Findings by severity" in body


def test_skills_loader_bundles_github_security_fix_skill() -> None:
    cached_load_skills_block.cache_clear()
    skill = skills_dir() / "github_security_fix" / "SKILL.md"
    assert skill.is_file()

    # Index carries the one-line catalog; the skill body loads on demand, so the
    # detailed assertions from #4727 belong against the body, not the block.
    assert "github-security-fix" in load_skills_index()
    body = load_skill_body("github-security-fix")
    assert "fix_github_security_alert" in body
    assert "security and quality issues" in body
    assert "/security/code-scanning" in body
    assert "Secret-scanning remediation" in body
    assert 'alert_type="auto"' in body
    assert 'alert_type="code_scanning"' in body
    assert 'alert_type="code_quality"' in body
    assert "auto-detected" in body
    assert "Never add coding-agent advice" in body
    assert "output exactly that text and stop" in body
    assert "reply in one short line" in body
    assert 'Do not say "next steps"' in body
    cached_load_skills_block.cache_clear()


def test_skills_loader_bundles_github_ci_fix_skill() -> None:
    cached_load_skills_block.cache_clear()
    skill = skills_dir() / "github_ci_fix" / "SKILL.md"
    assert skill.is_file()

    assert "github-ci-fix" in load_skills_index()
    body = load_skill_body("github-ci-fix")
    assert "fix_github_pr_ci" in body
    assert "output exactly that text and stop" in body
    assert '"next steps"' in body
    assert "separate linked git" in body
    assert "worktree, commits on a fresh" in body
    assert 'branch="main"' in body
    cached_load_skills_block.cache_clear()


def test_skill_matches_take_priority_over_generic_docs_answer() -> None:
    cached_load_skills_block.cache_clear()

    index = load_skills_index()
    body = load_skill_body("github-ci-fix-onboarding")
    prompt = build_action_system_prompt(_ctx())

    assert "Skill matches outrank a generic docs/how-to answer" in index
    assert '"onboard me"' in index
    assert "Can you onboard me on the CI/CD flow?" in body
    # Skills index still rides the assembled prompt after the markdown base.
    assert SKILLS_HEADER in prompt
    assert "github-ci-fix-onboarding" in prompt
    cached_load_skills_block.cache_clear()


def test_action_system_prompt_includes_context_blocks() -> None:
    prompt = build_action_system_prompt(
        _ctx(
            messages=[("user", "hello")],
            integrations=("github",),
            integrations_known=True,
        )
    )
    assert "CONNECTED INTEGRATIONS (this install, right now): github" in prompt
    assert "RECENT CONVERSATION" in prompt
    assert "architecture-audit" in prompt
    assert "skill_view" in prompt
    assert load_skill_body("architecture-audit") not in prompt


def test_skills_index_is_thin_relative_to_full_bodies() -> None:
    cached_load_skills_block.cache_clear()
    index = load_skills_index()
    bodies = "".join(load_skill_body(skill.name) for skill in list_action_skills())
    assert index.startswith(SKILLS_HEADER)
    assert "skill_view" in index.lower()
    assert len(bodies) > 10 * len(index)
    names = {skill.name for skill in list_action_skills()}
    assert names >= {
        "morning-report",
        "architecture-audit",
        "github-security-fix",
        "github-ci-fix",
    }
    for skill in list_action_skills():
        assert skill.name in index
        assert skill.description.split()[0] in index or skill.name in index


def test_action_system_prompt_includes_skills_block() -> None:
    prompt = build_action_system_prompt(_ctx())
    assert SKILLS_HEADER in prompt
    assert "morning-report" in prompt
    assert load_skill_body("morning-report") not in prompt
    # Skills sit after the markdown base so the action-planner identity is set first.
    assert prompt.index("You plan actions for the OpenSRE interactive shell.") < prompt.index(
        SKILLS_HEADER
    )
    # ...and before the per-turn context blocks that follow.
    assert prompt.index(SKILLS_HEADER) < prompt.index(
        "CONNECTED INTEGRATIONS (this install, right now):"
    )


def test_action_prompt_includes_long_term_memory_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_MEMORY_DISABLED_ENV
    from core.domain.memory import save_memory

    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)
    save_memory(
        slug="user-profile",
        memory_type="user",
        description="Name is Vaibhav",
        body="The user's name is Vaibhav on the platform team.",
    )
    prompt = build_action_system_prompt(_ctx())
    assert "LONG-TERM MEMORY" in prompt
    assert "user-profile" in prompt
    assert "platform team" in prompt


def test_scheduling_guidance_survives_prompt_assembly() -> None:
    """Stable half carries /cron + index; fat closer lives in the skill body.

    Thin harness: the weekday-8am closer is not inlined into every turn — it
    loads via skill_view when morning-report matches. The cacheable half must
    still name /cron and list morning-report as recurring so the agent knows
    to load and offer.
    """
    # Arrange
    from core.agent_harness.prompts import build_action_system_prompt_envelope

    snapshot = _ctx(messages=[("user", "give me a morning report")])

    # Act
    cached, _ephemeral = build_action_system_prompt_envelope(snapshot).render_split()
    assembled = " ".join(cached.lower().split())
    body = " ".join(load_skill_body("morning-report").lower().split())

    # Assert — recurring skill + thin index survive assembly; cron details live
    # in the skill body, not the markdown base.
    assert "morning-report" in assembled
    assert "recurring: weekdays 08:00" in assembled
    assert "skill_view" in assembled
    assert "propose_scheduled_delivery" in body
    assert "you plan actions for the opensre interactive shell" in assembled
    assert "propose_scheduled_delivery(" not in assembled


def test_the_slash_command_the_prompt_tells_the_agent_to_call_exists() -> None:
    """Guidance naming a command that is not registered would fail at run time.

    Pending offers expand to ``/cron add``; list/remove still use slash_invoke.
    Nothing else ties that string to the real command, so a rename of the CLI
    group would leave the agent confidently calling a command that is gone.
    """
    # Arrange
    from tools.interactive_shell.shared.slash_catalog import MCP_BY_COMMAND

    # Act
    entry = MCP_BY_COMMAND.get("/cron")
    loops_entry = MCP_BY_COMMAND.get("/loops")

    # Assert
    assert entry is not None, "the prompt offers /cron but it is not in the slash catalog"
    assert "add" in entry.llm_description.lower()
    assert loops_entry is not None, "the prompt offers /loops but it is not in the slash catalog"
    assert "add" in loops_entry.llm_description.lower()
    assert "delete" in loops_entry.llm_description.lower()
    assert "run once" in loops_entry.llm_description.lower()
    assert "stop" in loops_entry.llm_description.lower()
    assert "next fire time" in loops_entry.llm_description.lower()


def test_scheduling_is_never_offered_without_asking_first() -> None:
    """Creating a schedule unasked would be a surprise side effect.

    The business goal is an offer the user accepts, not silent automation. The
    recurring skill must gate creation on confirmation (the markdown base no
    longer inlines cron routing).
    """
    # Arrange
    load_skills_block.cache_clear()
    skill = _skill_instruction_text("morning-report")

    # Assert — structured propose tool; creation waits on confirm / yes
    assert "do not call /cron yet" in skill
    assert "propose_scheduled_delivery" in skill


def test_the_active_instruction_survives_context_truncation() -> None:
    """An oversized turn must lose stale history, never the current request.

    ``core.context_budget`` shrinks a message with ``text[:keep]`` — it keeps the
    head and drops the tail. Ephemeral history therefore has to sit *after* the
    literal user message: with history first, a turn over budget loses the very
    instruction that started it and the planner acts on stale context instead.
    """
    # Arrange
    from core.agent_harness.prompts import build_action_user_message
    from core.context_budget import _shrink_text

    instruction = "zzmarker-delete-the-staging-database"
    history = "--- Recent conversation ---\n" + ("user: earlier chatter\n" * 200)
    message = build_action_user_message(instruction, prefix=history)

    # Act
    shrunk, truncated = _shrink_text(message, 500)

    # Assert
    assert truncated is True
    assert instruction in shrunk


def test_the_cron_guidance_teaches_structured_schedule_offers() -> None:
    """Morning report must propose via tool, not scrape Want-me-to into /cron."""
    # Arrange
    skill = _skill_instruction_text("morning-report")

    assert "propose_scheduled_delivery" in skill
    assert "omit chat_id" in skill
    assert 'provider="slack"' in skill or "provider='slack'" in skill


# ── WAL: interrupted-turn recovery injection ─────────────────────────────────


def test_interrupted_turn_recovery_block_rides_the_ephemeral_tier() -> None:
    """The recovery note lands in the ephemeral half; the cached half is unchanged.

    Cache stability is the design constraint: the note rides exactly one turn,
    so it must never touch the byte-stable cached system prefix.
    """
    import dataclasses

    from core.agent_harness.prompts.action.assemble import (
        build_action_system_prompt_envelope,
        interrupted_turn_recovery_block,
    )

    note = (
        "A previous turn in this session was interrupted while tool calls were "
        "still executing (no result was recorded for them):\n"
        "- shell_run step-2 >> /tmp/demo_state.json (step 2)"
    )
    with_note = dataclasses.replace(_ctx(), recovery_note=note)

    assert interrupted_turn_recovery_block(_ctx()) == ""
    block = interrupted_turn_recovery_block(with_note)
    assert "INTERRUPTED-TURN RECOVERY" in block
    assert "shell_run step-2 >> /tmp/demo_state.json (step 2)" in block

    envelope_with = build_action_system_prompt_envelope(with_note)
    envelope_without = build_action_system_prompt_envelope(_ctx())
    assert envelope_with.render_cached() == envelope_without.render_cached()
    assert "INTERRUPTED-TURN RECOVERY" in envelope_with.render_ephemeral()
    assert "INTERRUPTED-TURN RECOVERY" not in envelope_without.render()
    assert "INTERRUPTED-TURN RECOVERY" in envelope_with.render()


def test_from_session_pops_the_pending_recovery_note() -> None:
    """The note is consumed by the first snapshot and never rides a second turn."""
    from types import SimpleNamespace

    session = SimpleNamespace(
        cli_agent_messages=[],
        configured_integrations=(),
        configured_integrations_known=False,
        reasoning_effort=None,
        pending_recovery_note="previous turn was interrupted while executing shell_run step-2",
    )

    first = TurnSnapshot.from_session("continue", session, surface=None)
    second = TurnSnapshot.from_session("next", session, surface=None)

    assert first.recovery_note is not None
    assert "shell_run step-2" in first.recovery_note
    assert session.pending_recovery_note is None
    assert second.recovery_note is None


def test_sequential_steps_rule_fragment_teaches_two_phase_state_writes() -> None:
    """Task-level WAL lives on the multi-step policy fragment (not the markdown base)."""
    from core.agent_harness.prompts.action.multi_step_policy import (
        ACTION_LOCAL_SHELL_MULTI_STEP_RULE,
    )

    prompt = " ".join(ACTION_LOCAL_SHELL_MULTI_STEP_RULE.lower().split())
    assert "two-phase" in prompt
    assert "`step n: started`" in prompt
    assert "`step n: committed`" in prompt
    assert "started-but-uncommitted step is re-run" in prompt
    assert "committed steps are never redone" in prompt
