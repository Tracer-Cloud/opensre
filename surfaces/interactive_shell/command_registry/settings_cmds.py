"""Slash commands: session settings (/auto, /trust, /effort, /verbose)."""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

import surfaces.interactive_shell.command_registry.repl_data as repl_data
from config.constants.llm import LLM_PROVIDER_ENV
from config.constants.repl_autonomy import (
    AUTO_LEVEL_CAPTIONS,
    DEFAULT_AUTO_LEVEL,
    AutoLevel,
    format_auto_status_plain,
    parse_auto_level,
)
from config.llm_reasoning_effort import (
    REASONING_EFFORT_OPTIONS,
    ReasoningEffort,
    describe_reasoning_effort_default,
    display_reasoning_effort,
    parse_reasoning_effort,
    provider_supports_reasoning_effort,
)
from infrastructure.observability.render.debug import verbose_output_enabled
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    resolve_provider_models,
)
from surfaces.shared.terminal.components.choice_menu import (
    repl_choose_one,
    repl_tty_interactive,
)

_TRUST_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("on", "enable trust mode (skip approval prompts)"),
    ("off", "disable trust mode"),
)

_AUTO_FIRST_ARGS: tuple[tuple[str, str], ...] = tuple(
    (level.value, AUTO_LEVEL_CAPTIONS[level]) for level in AutoLevel
)

_VERBOSE_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("on", "enable verbose logging"),
    ("off", "disable verbose logging"),
)


def _cmd_auto(session: Session, console: Console, args: list[str]) -> bool:
    if not args:
        console.print(f"[{HIGHLIGHT}]{format_auto_status_plain(session.terminal.auto_level)}[/]")
        console.print(
            f"[{DIM}]default:[/] {DEFAULT_AUTO_LEVEL.value} "
            f"({AUTO_LEVEL_CAPTIONS[DEFAULT_AUTO_LEVEL]})"
        )
        for level in AutoLevel:
            console.print(f"[{DIM}]  {level.value}[/] — {AUTO_LEVEL_CAPTIONS[level]}")
        console.print(
            f"[{DIM}]/trust on skips approval prompts even when /auto would ask. "
            "Session-only; not restored by /resume.[/]"
        )
        choices = ", ".join(level.value for level in AutoLevel)
        console.print(f"[{DIM}]usage:[/] /auto <{choices}>")
        return True
    parsed = parse_auto_level(args[0])
    if parsed is None:
        choices = ", ".join(level.value for level in AutoLevel)
        console.print(
            f"[{ERROR}]unknown auto level:[/] {escape(args[0])} [{DIM}](choices: {choices})[/]"
        )
        session.mark_latest(ok=False, kind="slash")
        return True
    session.terminal.auto_level = parsed
    console.print(f"[{HIGHLIGHT}]{format_auto_status_plain(parsed)}[/]")
    return True


_EFFORT_HELP: dict[ReasoningEffort, str] = {
    ReasoningEffort.LOW: "favor speed and lower reasoning cost",
    ReasoningEffort.MEDIUM: "balanced reasoning effort",
    ReasoningEffort.HIGH: "favor more thorough reasoning",
    ReasoningEffort.XHIGH: "favor deepest supported reasoning",
    ReasoningEffort.MAX: "alias for xhigh",
}

_EFFORT_FIRST_ARGS: tuple[tuple[str, str], ...] = tuple(
    (effort.value, _EFFORT_HELP[effort]) for effort in REASONING_EFFORT_OPTIONS
)


def _interactive_trust_menu(session: Session, console: Console) -> bool:
    current = "on" if session.terminal.trust_mode else "off"
    mode = repl_choose_one(
        title="Trust",
        breadcrumb="/trust",
        panel=True,
        current_value=current,
        initial_value=current,
        note="Session only. Enabling trust skips approval prompts, even when /auto would ask.",
        choices=[("off", "Off — follow approval policy"), ("on", "On — skip approval prompts")],
    )
    return True if mode is None else _cmd_trust(session, console, [mode])


def _cmd_trust(session: Session, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_trust_menu(session, console)

    if args and args[0].lower() in ("off", "false", "disable"):
        session.terminal.trust_mode = False
        console.print(f"[{DIM}]trust mode off[/]")
    else:
        session.terminal.trust_mode = True
        console.print(f"[{WARNING}]trust mode on[/] — future approval prompts will be skipped")
    return True


def _cmd_effort(session: Session, console: Console, args: list[str]) -> bool:
    settings = repl_data.load_llm_settings()
    provider = str(getattr(settings, "provider", os.getenv(LLM_PROVIDER_ENV, "anthropic")))
    reasoning_model = ""
    if settings is not None:
        reasoning_model, _toolcall_model = resolve_provider_models(settings, provider)
    supported_values = ", ".join(option.value for option in REASONING_EFFORT_OPTIONS)

    if not args:
        console.print(
            f"[{HIGHLIGHT}]reasoning effort:[/] {display_reasoning_effort(session.reasoning_effort)}"
        )
        console.print(
            f"[{DIM}]default config:[/] "
            f"{escape(describe_reasoning_effort_default(provider, reasoning_model))}"
        )
        console.print(f"[{DIM}]usage:[/] /effort <{supported_values}>")
        if not provider_supports_reasoning_effort(provider):
            console.print(
                f"[{DIM}]current provider {provider} ignores this setting; "
                "switch to openai or codex to use it.[/]"
            )
        return True

    effort = parse_reasoning_effort(args[0])
    if effort is None:
        console.print(
            f"[{ERROR}]unknown reasoning effort:[/] {escape(args[0])} "
            f"[{DIM}](choices: {supported_values})[/]"
        )
        session.mark_latest(ok=False, kind="slash")
        return True

    session.reasoning_effort = effort
    console.print(f"[{HIGHLIGHT}]reasoning effort set to:[/] {display_reasoning_effort(effort)}")
    if not provider_supports_reasoning_effort(provider):
        console.print(
            f"[{DIM}]current provider {provider} ignores this setting; "
            "switch to openai or codex to use it.[/]"
        )
    elif effort in {ReasoningEffort.XHIGH, ReasoningEffort.MAX}:
        console.print(
            f"[{DIM}]xhigh/max work best with newer GPT-5 or Codex models; "
            "older reasoning models may reject them.[/]"
        )
    return True


def _interactive_verbose_menu(_session: Session, console: Console) -> bool:
    current = "on" if verbose_output_enabled() else "off"
    mode = repl_choose_one(
        title="Verbose logging",
        breadcrumb="/verbose",
        panel=True,
        current_value=current,
        initial_value=current,
        note="Current process only. Enter applies; Esc leaves logging unchanged.",
        choices=[("off", "Off — normal output"), ("on", "On — include debug output")],
    )
    return True if mode is None else _cmd_verbose(_session, console, [mode])


def _cmd_verbose(_session: Session, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_verbose_menu(_session, console)

    if args and args[0].lower() in ("off", "false", "0", "disable"):
        os.environ.pop("TRACER_VERBOSE", None)
        console.print(f"[{DIM}]verbose logging off[/]")
    else:
        os.environ["TRACER_VERBOSE"] = "1"
        console.print(f"[{WARNING}]verbose logging on[/]")
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/auto",
        "Set tool-approval autonomy: off, low, med, or high.",
        _cmd_auto,
        usage=("/auto", "/auto med", "/auto high"),
        notes=(
            "Default is high (alpha): actions run without approval.",
            "off asks before every tool; "
            "med asks before mutating agent tools (shell, code, slash/CLI, …); "
            "high asks nothing.",
            "/trust on skips approval prompts even when /auto would ask.",
            "Session preference — not restored by /resume.",
        ),
        first_arg_completions=_AUTO_FIRST_ARGS,
    ),
    SlashCommand(
        "/trust",
        "Manage trust mode.",
        _cmd_trust,
        usage=("/trust", "/trust on", "/trust off"),
        notes=("In a TTY, bare /trust opens an interactive menu.",),
        first_arg_completions=_TRUST_FIRST_ARGS,
    ),
    SlashCommand(
        "/effort",
        "Set REPL reasoning effort.",
        _cmd_effort,
        usage=("/effort <low|medium|high|xhigh|max>",),
        first_arg_completions=_EFFORT_FIRST_ARGS,
    ),
    SlashCommand(
        "/verbose",
        "Manage verbose logging.",
        _cmd_verbose,
        usage=("/verbose", "/verbose on", "/verbose off"),
        notes=("In a TTY, bare /verbose opens an interactive menu.",),
        first_arg_completions=_VERBOSE_FIRST_ARGS,
    ),
]

__all__ = [
    "COMMANDS",
    "_AUTO_FIRST_ARGS",
    "_TRUST_FIRST_ARGS",
    "_VERBOSE_FIRST_ARGS",
    "_EFFORT_FIRST_ARGS",
]
