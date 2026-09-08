"""Structured schedule offers — yes expands without scraping Want-me-to prose."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_harness.prompts.memory.conversation import expand_affirmative_follow_up
from core.agent_harness.session.pending_offer import PendingScheduleOffer
from core.agent_harness.tools.tool_context import ActionToolScope
from core.agent_harness.turns.headless_adapters import InMemorySessionState, NoopTurnAccounting
from core.agent_harness.turns.orchestrator import run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from tools.interactive_shell.actions.propose_scheduled_delivery import (
    execute_propose_scheduled_delivery_tool,
)


def test_pending_recurring_skill_offer_includes_skill_flag() -> None:
    offer = PendingScheduleOffer(
        kind="recurring_skill",
        skill_name="morning-report",
        cron="0 8 * * 1-5",
        timezone="Europe/Amsterdam",
        provider="slack",
    )
    assert offer.to_slash_command() == (
        "/cron add --kind recurring_skill --cron '0 8 * * 1-5' "
        "--tz Europe/Amsterdam --provider slack --skill morning-report"
    )


def test_pending_morning_report_offer_preserves_city() -> None:
    offer = PendingScheduleOffer(
        kind="recurring_skill",
        skill_name="morning-report",
        skill_inputs={"city": "New Delhi"},
        cron="0 8 * * 1-5",
        timezone="Asia/Kolkata",
        provider="slack",
    )

    assert offer.to_slash_command() == (
        "/cron add --kind recurring_skill --cron '0 8 * * 1-5' "
        "--tz Asia/Kolkata --provider slack --skill morning-report --city 'New Delhi'"
    )


def test_pending_github_ci_health_offer_preserves_repository_scope() -> None:
    offer = PendingScheduleOffer(
        kind="recurring_skill",
        skill_name="github-ci-health",
        skill_inputs={
            "owner": "Tracer-Cloud",
            "repo": "opensre",
            "branch": "feature/ci health",
        },
        cron="0 8 * * 1-5",
        timezone="Europe/Istanbul",
        provider="slack",
    )

    assert offer.to_slash_command() == (
        "/cron add --kind recurring_skill --cron '0 8 * * 1-5' "
        "--tz Europe/Istanbul --provider slack --skill github-ci-health "
        "--owner Tracer-Cloud --repo opensre --branch 'feature/ci health'"
    )


def test_propose_github_ci_health_offer_requires_and_preserves_scope() -> None:
    session = InMemorySessionState()
    ctx = ActionToolScope(session=session, console=object())

    missing = execute_propose_scheduled_delivery_tool(
        {
            "kind": "recurring_skill",
            "skill_name": "github-ci-health",
            "cron": "0 8 * * 1-5",
            "provider": "interactive_shell",
        },
        ctx,
    )
    assert missing == {"ok": False, "error": "owner and repo are required for github-ci-health."}

    result = execute_propose_scheduled_delivery_tool(
        {
            "kind": "recurring_skill",
            "skill_name": "github-ci-health",
            "cron": "0 8 * * 1-5",
            "timezone": "Europe/Istanbul",
            "provider": "interactive_shell",
            "owner": "Tracer-Cloud",
            "repo": "opensre",
            "pr_number": "5961",
        },
        ctx,
    )

    assert result["ok"] is True
    assert session.pending_schedule_offer is not None
    assert session.pending_schedule_offer.skill_inputs == {
        "owner": "Tracer-Cloud",
        "repo": "opensre",
        "pr_number": "5961",
    }
    assert "--owner Tracer-Cloud --repo opensre --pr 5961" in result["slash_preview"]


def test_pending_offer_to_slash_omits_slack_chat_id() -> None:
    offer = PendingScheduleOffer(
        kind="manual_loop",
        cron="0 8 * * 1-5",
        timezone="Europe/Amsterdam",
        provider="slack",
        prompt="Check open incidents.",
    )
    assert offer.to_slash_command() == (
        "/cron add --kind manual_loop --cron '0 8 * * 1-5' "
        "--tz Europe/Amsterdam --provider slack --prompt 'Check open incidents.'"
    )


def test_yes_uses_pending_schedule_not_prose() -> None:
    pending = PendingScheduleOffer(
        kind="manual_loop",
        cron="0 9 * * 1",
        timezone="UTC",
        provider="telegram",
        chat_id="-100123",
        prompt="Check open incidents.",
    )
    history = [
        (
            "assistant",
            "Delivered.\nWant me to: schedule this as a manual_loop every "
            "weekday at 8am to the same channel?",
        ),
    ]
    expanded = expand_affirmative_follow_up("yes", history, pending_schedule=pending)
    assert expanded == (
        "/cron add --kind manual_loop --cron '0 9 * * 1' "
        "--tz UTC --provider telegram --prompt 'Check open incidents.' "
        "--chat-id -100123"
    )
    assert "1-5" not in expanded
    assert "same channel" not in expanded


def test_propose_tool_sets_session_pending_offer() -> None:
    session = InMemorySessionState()
    session.record(
        "shell",
        "curl -s 'wttr.in/Amsterdam?format=3'",
        ok=True,
        response_text="Amsterdam: ☀️ +20°C",
    )
    session.record(
        "shell",
        "curl -s 'https://feeds.bbci.co.uk/news/rss.xml' | head",
        ok=True,
        response_text="Some headline",
    )
    ctx = ActionToolScope(session=session, console=object())
    briefing = (
        "Good morning! Here is your briefing.\n"
        "Weather — Amsterdam: ☀️ +20°C\n"
        "Top headlines:\n"
        "- Some headline"
    )
    result = execute_propose_scheduled_delivery_tool(
        {
            "kind": "recurring_skill",
            "skill_name": "morning-report",
            "cron": "0 8 * * 1-5",
            "timezone": "UTC",
            "provider": "slack",
            "chat_id": "C0123ABCD",
            "briefing_text": briefing,
            "city": "Amsterdam",
        },
        ctx,
    )
    assert result["ok"] is True
    assert session.pending_schedule_offer is not None
    assert session.pending_schedule_offer.kind == "recurring_skill"
    assert session.pending_schedule_offer.skill_name == "morning-report"
    assert session.pending_schedule_offer.skill_inputs == {"city": "Amsterdam"}
    assert "--city Amsterdam" in result["slash_preview"]
    assert result["closer"].startswith("**Want me to:**")
    assert "Weather — Amsterdam" in result["response_text"]
    assert result["closer"] in result["response_text"]


def test_propose_manual_loop_offer_preserves_prompt_through_confirmation() -> None:
    session = InMemorySessionState()
    session.record(
        "shell",
        "curl -s 'wttr.in/Amsterdam?format=3'",
        ok=True,
        response_text="Amsterdam: ☀️ +20°C",
    )
    session.record(
        "shell",
        "curl -s 'https://feeds.bbci.co.uk/news/rss.xml' | head",
        ok=True,
        response_text="Some headline",
    )
    ctx = ActionToolScope(session=session, console=object())
    prompt = "Check open incidents and summarize production risk."

    result = execute_propose_scheduled_delivery_tool(
        {
            "kind": "manual_loop",
            "cron": "0 8 * * 1-5",
            "timezone": "UTC",
            "provider": "interactive_shell",
            "prompt": prompt,
            "briefing_text": (
                "Good morning! Weather — Amsterdam: ☀️ +20°C\nTop headlines:\n- Some headline"
            ),
        },
        ctx,
    )

    assert result["ok"] is True
    assert session.pending_schedule_offer is not None
    assert session.pending_schedule_offer.prompt == prompt
    assert f"--prompt '{prompt}'" in result["slash_preview"]
    expanded = expand_affirmative_follow_up(
        "yes", [], pending_schedule=session.pending_schedule_offer
    )
    assert f"--prompt '{prompt}'" in expanded


def test_propose_alone_without_briefing_work_is_rejected() -> None:
    """User symptom: 'give me a morning report' → only Want me to, no weather."""
    session = InMemorySessionState()
    ctx = ActionToolScope(session=session, console=object())
    result = execute_propose_scheduled_delivery_tool(
        {
            "kind": "recurring_skill",
            "skill_name": "morning-report",
            "cron": "0 8 * * 1-5",
            "timezone": "UTC",
            "provider": "slack",
            "chat_id": "C0123ABCD",
        },
        ctx,
    )
    assert result["ok"] is False
    assert session.pending_schedule_offer is None
    assert (
        "fetch" in str(result.get("error", "")).lower()
        or "briefing" in str(result.get("error", "")).lower()
    )


def test_run_turn_consumes_pending_schedule_on_yes() -> None:
    session = InMemorySessionState()
    session.pending_schedule_offer = PendingScheduleOffer(
        kind="manual_loop",
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider="slack",
        prompt="Check open incidents.",
    )
    seen: list[str] = []

    def execute_actions(text: str, **_kwargs: object) -> ToolCallingTurnResult:
        seen.append(text)
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="ok",
        )

    run_turn(
        "yes",
        session,
        execute_actions=execute_actions,
        accounting=NoopTurnAccounting(),
    )

    assert len(seen) == 1
    assert seen[0].startswith("/cron add")
    assert session.pending_schedule_offer is None


def test_a_confirmed_schedule_survives_the_literal_slash_dispatcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cron expression must reach the CLI as ONE argument.

    ``PendingScheduleOffer`` removes the model from string-building, but the
    confirmed offer still travels as slash text that
    ``_literal_slash_tool_call`` tokenises. Space-joined, the five-field cron
    expression arrives as five arguments and ``cron add`` dies with "Got
    unexpected extra arguments" — the same failure, now deterministic.
    """
    # Arrange
    from click.testing import CliRunner

    from core.agent_harness.session.pending_offer import PendingScheduleOffer
    from core.agent_harness.turns.action_driver import _literal_slash_tool_call
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
    from surfaces.cli.commands.cron import cron_add

    class _SlashTool:
        name = "slash_invoke"

    monkeypatch.setattr(
        scheduler_store,
        "default_task_store_path",
        lambda: tmp_path / "scheduler_tasks.json",
    )

    offer = PendingScheduleOffer(
        kind="manual_loop",
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider="interactive_shell",
        prompt="Check open incidents.",
    )

    # Act
    call = _literal_slash_tool_call(offer.to_slash_command(), [_SlashTool()])

    # Assert
    assert call is not None
    args = call.input["args"]
    assert "0 8 * * 1-5" in args, f"cron expression was fragmented: {args}"
    result = CliRunner().invoke(cron_add, args[1:])
    assert result.exit_code == 0, result.output


def test_slash_tool_rebuild_keeps_cron_expression_for_dispatch() -> None:
    """execute_slash_tool must not flatten args into an unquoted command line.

    Real failure mode from the shell: yes → /cron add … printed as
    ``$ /cron add --cron 0 8 * * 1-5`` then Click saw five extra positionals.
    The tool call already had the cron as one arg; joining with spaces for
    dispatch/display was what destroyed it.
    """
    import shlex

    from core.agent_harness.session.pending_offer import PendingScheduleOffer
    from core.agent_harness.turns.action_driver import _literal_slash_tool_call
    from tools.interactive_shell.actions.slash import execute_slash_tool

    class _SlashTool:
        name = "slash_invoke"

    class _Ports:
        def command_exists(self, name: str) -> bool:
            return name == "/cron"

        def command_is_mutating(self, _name: str) -> bool:
            return True

        def tty_interactive(self) -> bool:
            return False

        def format_turn_outcome(self, command: str, *, ok: bool) -> str:
            return f"{command}:{ok}"

        def execution_allowed(self, **_kwargs: object) -> bool:
            return True

        def dispatch(self, command: str, **_kwargs: object) -> bool:
            dispatched.append(command)
            return True

    offer = PendingScheduleOffer(
        kind="manual_loop",
        cron="0 8 * * 1-5",
        timezone="Europe/Amsterdam",
        provider="slack",
        prompt="Check open incidents.",
    )
    call = _literal_slash_tool_call(offer.to_slash_command(), [_SlashTool()])
    assert call is not None

    dispatched: list[str] = []
    from rich.console import Console

    from core.agent_harness.tools.tool_context import ActionToolScope
    from core.agent_harness.turns.headless_adapters import InMemorySessionState

    ctx = ActionToolScope(
        session=InMemorySessionState(),
        console=Console(force_terminal=False, highlight=False),
        slash_ports=_Ports(),
        is_tty=False,
    )
    execute_slash_tool(call.input, ctx)

    assert len(dispatched) == 1
    line = dispatched[0]
    tokens = shlex.split(line, posix=True)
    assert "0 8 * * 1-5" in tokens, f"cron fragmented in dispatched line: {line!r} → {tokens}"
    # Naive space-join shows up as an unquoted `--cron 0 8`; quoted rebuild keeps one word.
    assert "'0 8 * * 1-5'" in line or '"0 8 * * 1-5"' in line, line


def test_an_apostrophe_in_a_typed_slash_command_still_dispatches() -> None:
    """Tokenising must not start rejecting ordinary prose after a slash."""
    # Arrange
    from core.agent_harness.turns.action_driver import _literal_slash_tool_call

    class _SlashTool:
        name = "slash_invoke"

    # Act
    call = _literal_slash_tool_call("/goal don't know why", [_SlashTool()])

    # Assert
    assert call is not None
    assert call.input["command"] == "/goal"


def test_the_offer_tool_does_not_advertise_itself_as_the_way_to_run_a_report() -> None:
    """Tool metadata must not attract the request it comes *after*.

    The description used to open "After delivering a recurring briefing (e.g.
    morning report)…". Asked for a morning report, the model matched that noun
    and called this tool as the whole turn — the user got an offer to repeat a
    briefing that was never produced. The precondition has to lead, and the
    example noun has to go.
    """
    # Arrange
    from tools.interactive_shell.actions.propose_scheduled_delivery import (
        propose_scheduled_delivery_tool as tool,
    )

    # Assert
    assert "morning report" not in tool.description.lower()
    assert "precondition" in tool.description.lower()
    assert tool.anti_examples, "no anti-example steers it away from the initial request"
    # Assert the property, not one phrasing: some anti-example must tie the
    # request shape to an ordering constraint.
    steered = [
        example.lower()
        for example in tool.anti_examples
        if ("report" in example.lower() or "briefing" in example.lower())
        and any(word in example.lower() for word in ("first", "last", "before"))
    ]
    assert steered, f"no anti-example orders it after the work: {tool.anti_examples}"


def test_the_skill_forbids_offering_before_the_work() -> None:
    """The recipe must state the ordering, not merely imply it by step number."""
    # Arrange
    from core.agent_harness.prompts.skills.loader import skills_dir

    raw = (skills_dir() / "morning_report" / "SKILL.md").read_text(encoding="utf-8")
    body = " ".join(raw.replace("`", "").lower().split())

    # Assert
    assert "never call propose_scheduled_delivery as the first or only tool" in body


def test_a_failed_schedule_keeps_the_offer_for_a_second_try() -> None:
    """A rejected /cron add must not burn the user's accepted offer.

    The pending offer was cleared as soon as the affirmative expanded, before
    the command ran. When ``cron add`` exited non-zero the offer was gone, so a
    second "yes" expanded nothing and the user had to re-ask for the whole
    briefing to get another offer.
    """
    # Arrange
    from core.agent_harness.turns.headless_adapters import (
        InMemorySessionState,
        NoopTurnAccounting,
    )
    from core.agent_harness.turns.orchestrator import run_turn
    from core.agent_harness.turns.turn_results import ToolCallingTurnResult

    session = InMemorySessionState()
    session.pending_schedule_offer = PendingScheduleOffer(
        kind="manual_loop",
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider="slack",
        prompt="Check open incidents.",
    )

    def _execute_failing(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
        )

    # Act
    run_turn(
        "yes",
        session,
        execute_actions=_execute_failing,
        accounting=NoopTurnAccounting(),
    )

    # Assert
    assert session.pending_schedule_offer is not None, "offer burned by a failed add"


def test_a_successful_schedule_consumes_the_offer() -> None:
    """The mirror case: once it lands, a later bare yes must not re-add it."""
    # Arrange
    from core.agent_harness.turns.headless_adapters import (
        InMemorySessionState,
        NoopTurnAccounting,
    )
    from core.agent_harness.turns.orchestrator import run_turn
    from core.agent_harness.turns.turn_results import ToolCallingTurnResult

    session = InMemorySessionState()
    session.pending_schedule_offer = PendingScheduleOffer(
        kind="manual_loop",
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider="slack",
        prompt="Check open incidents.",
    )

    def _execute_ok(_text: str, **_kwargs: object) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="Task abc123 created.",
        )

    # Act
    run_turn(
        "yes",
        session,
        execute_actions=_execute_ok,
        accounting=NoopTurnAccounting(),
    )

    # Assert
    assert session.pending_schedule_offer is None


def test_a_stale_fetch_from_an_earlier_turn_does_not_unlock_the_offer() -> None:
    """Evidence must come from THIS turn, not anywhere in the session.

    The guard scanned the whole of ``session.history``, so a wttr.in call from
    any earlier turn satisfied it. A later turn could then offer to schedule a
    briefing it never produced — the exact failure the guard exists to stop,
    reached through a different door.
    """
    # Arrange
    from core.agent_harness.tools.tool_context import ActionToolScope
    from core.agent_harness.turns.headless_adapters import InMemorySessionState
    from tools.interactive_shell.actions.propose_scheduled_delivery import (
        execute_propose_scheduled_delivery_tool,
    )

    session = InMemorySessionState()
    # An earlier turn fetched weather and news.
    session.record("shell", "curl -s 'wttr.in/Amsterdam?format=3'", ok=True)
    session.record("shell", "curl -s 'https://feeds.bbci.co.uk/news/rss.xml'", ok=True)
    # This turn starts here and fetches nothing.
    ctx = ActionToolScope(session=session, console=object(), history_start=len(session.history))

    # Act
    result = execute_propose_scheduled_delivery_tool(
        {
            "kind": "manual_loop",
            "cron": "0 8 * * 1-5",
            "provider": "slack",
            "chat_id": "C0123ABCD",
            "prompt": "Check open incidents.",
            "briefing_text": "Good morning! Weather — Amsterdam: +20C\nTop headlines:\n- one",
        },
        ctx,
    )

    # Assert
    assert result["ok"] is False, "a stale fetch unlocked a fresh offer"
    assert session.pending_schedule_offer is None


def test_the_turn_boundary_reaches_the_tool_context_in_production() -> None:
    """The guard defaults to 0, so a provider that forgets this stays session-wide.

    A silent default is the failure mode here: everything still runs, the guard
    just quietly reverts to scanning the whole session.
    """
    # Arrange
    from core.agent_harness.tools.tool_provider import DefaultToolProvider
    from core.agent_harness.turns.headless_adapters import InMemorySessionState

    session = InMemorySessionState()
    session.record("shell", "curl -s 'wttr.in/Amsterdam?format=3'", ok=True)
    session.record("shell", "curl -s 'https://feeds.bbci.co.uk/news/rss.xml'", ok=True)

    provider = DefaultToolProvider(session, object())

    # Act
    provider.action_tools(confirm_fn=None, is_tty=False)
    ctx = provider._tool_scope

    # Assert
    assert ctx is not None
    assert ctx.history_start == 2, "boundary must exclude rows already in the session"
