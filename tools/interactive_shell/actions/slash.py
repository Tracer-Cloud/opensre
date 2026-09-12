"""Slash command tool."""

from __future__ import annotations

import shlex
from typing import Any

from rich.markup import escape

from core.agent_harness.spi.session_state import (
    exclusive_stdin_active,
    session_terminal,
    set_auto_command,
)
from core.agent_harness.tools import (
    ActionToolScope,
    capability_available_from_sources,
    execute_with_action_context,
)
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from tools.interactive_shell.shared import plan_foreground_tool
from tools.interactive_shell.shared.slash_catalog import (
    slash_invoke_input_schema,
    slash_invoke_tool_description,
)

# Slash commands that drive a raw-stdin inline picker or wizard (questionary /
# repl_choose_one). When the action agent resolves free text (e.g. "remove
# github") into one of these, the REPL loop has NOT reserved exclusive stdin for
# the turn — it only does so for deterministically-typed commands. Running the
# picker inline then races the concurrently open prompt_async() for stdin and the
# terminal's cursor-position replies (ESC[row;colR) leak into the input line as
# literal keystrokes. Defer them through ``set_auto_command`` so the loop
# re-dispatches the command as a deterministic turn it runs with exclusive stdin.
_INTERACTIVE_PICKER_MENUS: frozenset[str] = frozenset(
    {"/auth", "/choose", "/login", "/integrations", "/mcp"}
)
_INTERACTIVE_PICKER_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("/auth", "login"),
        ("/auth", "logout"),
        ("/integrations", "setup"),
        ("/integrations", "remove"),
        ("/loops", "show"),
        ("/mcp", "connect"),
        ("/mcp", "disconnect"),
    }
)


def _slash_drives_interactive_picker(
    name: str,
    slash_args: list[str],
    *,
    session: Any,
    is_tty: bool | None,
    ports: Any,
) -> bool:
    """True when a planned slash command opens a raw-stdin inline picker/wizard.

    Only relevant in an interactive REPL with a terminal facet: gateway/headless
    sessions always run inline, and non-TTY turns must not queue back to a REPL
    loop that does not exist (e.g. gateway running under tmux with a TTY stdin).
    """
    if is_tty is False or session_terminal(session) is None:
        return False
    if not ports.tty_interactive():
        return False
    if name == "/login":
        return True
    if not slash_args:
        return name in _INTERACTIVE_PICKER_MENUS
    return (name, slash_args[0].lower()) in _INTERACTIVE_PICKER_SUBCOMMANDS and (
        name != "/loops" or len(slash_args) == 1
    )


# Cap the failure excerpt fed back to the model: enough for a usage/typo
# message, small enough to never bloat the observation.
_MAX_OBSERVED_ERROR_CHARS = 700
# Success output gets more room: tables (e.g. ``/cron list`` task ids) must
# survive so the model can chain a data-dependent follow-up call.
_MAX_OBSERVED_OUTPUT_CHARS = 2000


def _dispatch_and_translate_exit(
    command: str, ctx: ActionToolScope, **kwargs: Any
) -> bool | dict[str, Any]:
    should_continue = ctx.slash_ports.dispatch(
        command,
        session=ctx.session,
        console=ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        **kwargs,
    )
    if not should_continue and ctx.request_exit is not None:
        ctx.request_exit()
    return _slash_observation(ctx, command)


def _slash_observation(ctx: ActionToolScope, command: str) -> bool | dict[str, Any]:
    """Build the model-facing result from the slash row this dispatch recorded.

    ``dispatch_slash`` records a history row for the command with an outcome
    ``response_text`` (captured console output, or an error/usage excerpt when
    the handler or a delegated CLI subprocess fails). Without reading that row
    back, the tool observation is always ``{"ok": true}``: the agent cannot
    react to a failure (e.g. retry ``/cron remove`` with the task id it
    forgot) and cannot chain a data-dependent follow-up (e.g. read task ids
    out of ``/cron list``). Rows from earlier turns are not evidence: only
    look at what THIS turn appended.
    """
    history = getattr(ctx.session, "history", None) or []
    rows = history[getattr(ctx, "history_start", 0) :]
    for row in reversed(rows):
        if not isinstance(row, dict) or row.get("type") != "slash":
            continue
        if row.get("text") != command:
            continue
        if row.get("ok", True):
            output = str(row.get("response_text") or "").strip()
            if not output:
                return True
            return {"ok": True, "output": output[:_MAX_OBSERVED_OUTPUT_CHARS]}
        error = str(row.get("response_text") or "").strip()
        return {
            "ok": False,
            "command": command,
            "error": error[:_MAX_OBSERVED_ERROR_CHARS] or f"{command} failed (non-zero exit code)",
        }
    # No row found (recording deferred or handler-owned): assume success.
    return True


def _slash_arg_for_join(parts_so_far: list[str], arg: str) -> str:
    """Quote ``arg`` only when embedding it would break a later ``shlex.split``.

    Blind ``shlex.join`` quotes harmless tokens like ``days?`` and ``PostHog;``
    on the ``$ …`` banner even though the typed ``❯`` line had no quotes.
    Progressive round-trip keeps cron expressions / spaced args quoted and
    leaves ordinary prose readable.
    """
    if arg == "":
        return "''"
    candidate_parts = [*parts_so_far, arg]
    candidate = " ".join(candidate_parts)
    try:
        if shlex.split(candidate, posix=True) == candidate_parts:
            return arg
    except ValueError:
        # Unbalanced quotes in the joined line: it cannot round-trip as-is, so
        # fall through to quoting.
        pass
    return shlex.quote(arg)


def _join_slash_command(command: str, parsed_args: list[str]) -> str:
    """Rebuild a dispatchable slash line without flattening spaced arguments.

    ``slash_invoke`` already carries each CLI token separately (e.g. a five-field
    cron expression as one arg). A plain ``" ".join`` turns that back into an
    unquoted line that ``dispatch_slash`` later splits on spaces — ``cron add``
    then dies with unexpected extra arguments. Quote only when a later
    ``shlex.split`` would not recover the same tokens.
    """
    if not parsed_args:
        return command
    parts = [command]
    for arg in parsed_args:
        parts.append(_slash_arg_for_join(parts, arg))
    return " ".join(parts)


def _slash_line_parts(stripped: str) -> list[str]:
    """Tokenise a slash line, keeping quoted spans (cron, paths) intact."""
    try:
        return shlex.split(stripped, posix=True)
    except ValueError:
        return stripped.split()


def execute_slash_tool(args: dict[str, Any], ctx: ActionToolScope) -> bool | dict[str, Any]:
    if ctx.slash_ports is None:
        raise RuntimeError("slash tool requires slash runtime ports")
    command = str(args.get("command", "")).strip()
    raw_args = args.get("args")
    parsed_args = [str(item).strip() for item in raw_args] if isinstance(raw_args, list) else []
    stripped = _join_slash_command(command, parsed_args).strip()
    if stripped == "/" or not stripped:
        return _dispatch_and_translate_exit(
            stripped or "/",
            ctx,
        )

    parts = _slash_line_parts(stripped)
    name = parts[0].lower() if parts else ""
    slash_args = parts[1:]
    if not ctx.slash_ports.command_exists(name):
        return _dispatch_and_translate_exit(
            stripped,
            ctx,
        )

    if _slash_drives_interactive_picker(
        name,
        slash_args,
        session=ctx.session,
        is_tty=ctx.is_tty,
        ports=ctx.slash_ports,
    ) and not exclusive_stdin_active(ctx.session):
        # Hand the picker back to the REPL loop instead of running it against the
        # live prompt: set_auto_command re-submits it as a deterministic turn
        # the loop dispatches with exclusive stdin, so no CPR replies leak in.
        # Do not record a slash history row here — dispatch_slash will record when
        # the queued command runs.
        #
        # Nothing is printed: set_auto_command prefills the prompt and submits it,
        # so the command is already echoed on the input line. Announcing it here
        # as well showed the same command twice before it had even run.
        set_auto_command(ctx.session, stripped)
        return True

    # Control commands (exit/quit) never mutate state, so they run without the
    # execution gate — a standing plan-only request must not trap the user in
    # the shell.
    if not ctx.slash_ports.command_is_mutating(name):
        return _dispatch_and_translate_exit(stripped, ctx, policy_precleared=True)

    plan = plan_foreground_tool("slash", "slash")
    if not ctx.slash_ports.execution_allowed(
        policy=plan.policy,
        session=ctx.session,
        console=ctx.console,
        action_summary=stripped,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    ):
        ctx.session.record(
            "slash",
            stripped,
            ok=False,
            response_text=ctx.slash_ports.format_turn_outcome(stripped, ok=False),
        )
        return True

    # Announce the command unless the input line already did. Exclusive stdin is
    # only reserved for a *literally typed* slash command (see
    # ``turn_needs_exclusive_stdin``), so when it is active the prompt above
    # already shows this command and a banner would repeat it. On every other
    # path the agent resolved free text into a slash — nothing was echoed, and
    # this banner is the only indication of what is about to run.
    if not exclusive_stdin_active(ctx.session):
        ctx.console.print(f"[bold]$ {escape(stripped)}[/bold]")
    return _dispatch_and_translate_exit(
        stripped,
        ctx,
        policy_precleared=True,
    )


def run_slash(*, command: str, args: list[str] | None = None, context: Any) -> dict[str, Any]:
    return execute_with_action_context(
        {"command": command, "args": args or []},
        context,
        execute_slash_tool,
    )


slash_invoke_tool = RegisteredTool(
    name="slash_invoke",
    description=slash_invoke_tool_description(),
    input_schema=slash_invoke_input_schema(),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    accepts_runtime_context=True,
    run=run_slash,
    is_available=lambda sources: capability_available_from_sources(sources, "slash_commands"),
)


__all__ = ["execute_slash_tool", "slash_invoke_tool"]
