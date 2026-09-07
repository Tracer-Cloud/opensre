"""Slash command /model and interactive provider/model menus."""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

from config.constants.llm import LLM_PROVIDER_ENV
from surfaces.interactive_shell.command_registry.model.presentation import render_current_models
from surfaces.interactive_shell.command_registry.model.switching import (
    _provider_allows_custom_models,
    restore_default_model,
    switch_llm_provider,
    switch_reasoning_model,
    switch_toolcall_model,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING
from surfaces.shared.llm_setup.provider_choices import (
    OTHER_PROVIDER_SELECTION,
    focused_setup_provider_options,
    other_setup_provider_options,
)
from surfaces.shared.terminal.components.choice_menu import (
    CRUMB_SEP,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)

_ROOT = "/model"  # breadcrumb root label


def _provider_menu_choices() -> list[tuple[str, str]]:
    current_provider = (os.getenv(LLM_PROVIDER_ENV, "anthropic") or "anthropic").strip().lower()
    options: list[tuple[str, str]] = []
    for provider in focused_setup_provider_options():
        suffix = "*" if provider.value == current_provider else ""
        options.append((provider.value, f"{provider.value}{suffix}"))
    suffix = "*" if current_provider not in {value for value, _label in options} else ""
    options.append((OTHER_PROVIDER_SELECTION, f"other provider{suffix}"))
    return options


def _other_provider_menu_choices() -> list[tuple[str, str]]:
    current_provider = (os.getenv(LLM_PROVIDER_ENV, "anthropic") or "anthropic").strip().lower()
    options: list[tuple[str, str]] = []
    for provider in other_setup_provider_options():
        suffix = "*" if provider.value == current_provider else ""
        options.append((provider.value, f"{provider.value}{suffix}"))
    return options


def _choose_provider_value(*, title: str, breadcrumb: str) -> str | None:
    provider_value = repl_choose_one(
        title=title,
        breadcrumb=breadcrumb,
        choices=_provider_menu_choices(),
    )
    if provider_value != OTHER_PROVIDER_SELECTION:
        return provider_value
    return repl_choose_one(
        title="other provider",
        breadcrumb=f"{breadcrumb}{CRUMB_SEP}other",
        choices=_other_provider_menu_choices(),
    )


def _reasoning_model_menu_choices(provider: object) -> list[tuple[str, str]]:
    model_options = list(getattr(provider, "models", ()))
    choices: list[tuple[str, str]] = [
        ("__provider_default__", "provider default (one step)"),
    ]
    for option in model_options:
        value = str(getattr(option, "value", ""))
        display = value if value else "cli-default"
        choices.append((value, display))
    if _provider_allows_custom_models(provider):
        choices.append(("__custom__", "custom model ID"))
    return choices


def _toolcall_model_menu_choices(provider: object) -> list[tuple[str, str]]:
    model_options = list(getattr(provider, "models", ()))
    choices: list[tuple[str, str]] = [
        ("__keep__", "keep"),
        ("__match_reasoning__", "match-reasoning"),
    ]
    for option in model_options:
        value = str(getattr(option, "value", ""))
        display = value if value else "cli-default"
        choices.append((value, display))
    if _provider_allows_custom_models(provider):
        choices.append(("__custom__", "custom model ID"))
    return choices


def _prompt_custom_model_id(console: Console, provider_value: str = "provider") -> str | None:
    """Prompt the user to type a custom model ID."""
    console.print()
    console.print(
        f"[{DIM}]Enter a model ID for {escape(provider_value)}. "
        "The provider will validate availability when OpenSRE sends a request.[/]"
    )
    try:
        value = console.input(f"[{HIGHLIGHT}]model ID> [/]").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return value if value else None


def _interactive_set_provider(console: Console) -> bool | None:
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    crumb_set = f"{_ROOT}{CRUMB_SEP}set"
    while True:
        provider_value = _choose_provider_value(
            title="LLM provider",
            breadcrumb=crumb_set,
        )
        if provider_value is None:
            return None
        provider = PROVIDER_BY_VALUE.get(provider_value)
        if provider is None:
            return False

        crumb_model = f"{crumb_set}{CRUMB_SEP}{provider_value}"
        while True:
            reasoning_choice = repl_choose_one(
                title="reasoning model",
                breadcrumb=crumb_model,
                choices=_reasoning_model_menu_choices(provider),
            )
            if reasoning_choice is None:
                break

            if reasoning_choice == "__custom__":
                custom = _prompt_custom_model_id(console, provider.value)
                if custom is None:
                    continue
                reasoning_choice = custom

            model_choice = (
                None if reasoning_choice == "__provider_default__" else str(reasoning_choice)
            )
            toolcall_model: str | None = None
            # Default reasoning: switch provider + default reasoning only — do not
            # prompt for toolcall (matches non-interactive `/model set <provider>`).
            if provider.toolcall_model_env and reasoning_choice != "__provider_default__":
                crumb_tc = f"{crumb_model}{CRUMB_SEP}toolcall"
                while True:
                    toolcall_value = repl_choose_one(
                        title="toolcall model",
                        breadcrumb=crumb_tc,
                        choices=_toolcall_model_menu_choices(provider),
                    )
                    if toolcall_value is None:
                        return None
                    if toolcall_value == "__keep__":
                        break
                    if toolcall_value == "__match_reasoning__":
                        toolcall_model = model_choice or provider.default_model
                        break
                    if toolcall_value == "__custom__":
                        custom_tc = _prompt_custom_model_id(console, provider.value)
                        if custom_tc is None:
                            continue
                        toolcall_model = custom_tc
                        break
                    toolcall_model = str(toolcall_value)
                    break

            return switch_llm_provider(
                provider.value,
                console,
                model=model_choice,
                toolcall_model=toolcall_model,
            )


def _interactive_restore_provider(console: Console) -> bool | None:
    provider_value = _choose_provider_value(
        title="LLM provider",
        breadcrumb=f"{_ROOT}{CRUMB_SEP}restore",
    )
    if provider_value is None:
        return None
    return restore_default_model(provider_value, console)


def _interactive_set_toolcall(console: Console) -> bool | None:
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    crumb_tc = f"{_ROOT}{CRUMB_SEP}toolcall"
    provider_value = _choose_provider_value(
        title="LLM provider",
        breadcrumb=crumb_tc,
    )
    if provider_value is None:
        return None
    provider = PROVIDER_BY_VALUE.get(provider_value)
    if provider is None:
        return False
    if not provider.toolcall_model_env:
        console.print(
            f"[{WARNING}]provider {provider.value} does not expose a separate "
            "toolcall model[/] — nothing to set."
        )
        return False
    model_value = repl_choose_one(
        title="toolcall model",
        breadcrumb=f"{crumb_tc}{CRUMB_SEP}{provider_value}",
        choices=_toolcall_model_menu_choices(provider),
    )
    if model_value is None:
        return None
    if model_value == "__keep__":
        console.print("[dim]toolcall model left unchanged.[/dim]")
        return True
    if model_value == "__match_reasoning__":
        reasoning = (os.getenv(provider.model_env, "") or "").strip() or provider.default_model
        return switch_toolcall_model(reasoning, console, provider_name=provider.value)
    if model_value == "__custom__":
        custom_tc = _prompt_custom_model_id(console, provider.value)
        if custom_tc is None:
            return None
        model_value = custom_tc
    return switch_toolcall_model(str(model_value), console, provider_name=provider.value)


def _interactive_model_menu(session: Session, console: Console) -> bool:
    while True:
        action = repl_choose_one(
            title="Select Model and Effort",
            breadcrumb=f"{_ROOT}",
            choices=[
                ("show", "show"),
                ("set", "set"),
                ("restore", "restore"),
                ("toolcall", "toolcall"),
                ("done", "done"),
            ],
        )
        if action is None or action == "done":
            return True
        if action == "show":
            repl_section_break(console)
            render_current_models(console)
            repl_section_break(console)
            continue
        if action == "set":
            switched = _interactive_set_provider(console)
            if switched is None:
                continue
            if not switched:
                session.mark_latest(ok=False, kind="slash")
                repl_section_break(console)
                continue
            return True
        if action == "restore":
            restored = _interactive_restore_provider(console)
            if restored is None:
                continue
            if not restored:
                session.mark_latest(ok=False, kind="slash")
                repl_section_break(console)
                continue
            return True
        if action == "toolcall":
            switched = _interactive_set_toolcall(console)
            if switched is None:
                continue
            if not switched:
                session.mark_latest(ok=False, kind="slash")
                repl_section_break(console)
                continue
            return True


def parse_model_set_args(args: list[str]) -> tuple[str, str | None, str | None]:
    """Parse `set <provider> [reasoning_model] [--toolcall-model <m>]`.

    ``args`` is the slice after the ``set``/``use``/``switch`` keyword.

    Raises :class:`ValueError` with a user-facing message when the input is
    malformed.
    """
    if not args:
        raise ValueError("missing provider name")

    provider = args[0]
    reasoning_model: str | None = None
    toolcall_model: str | None = None

    i = 1
    while i < len(args):
        token = args[i]
        if token == "--toolcall-model":
            if i + 1 >= len(args):
                raise ValueError("missing value for --toolcall-model")
            toolcall_model = args[i + 1]
            i += 2
            continue
        if token.startswith("--"):
            raise ValueError(f"unknown flag: {token}")
        if reasoning_model is not None:
            raise ValueError(f"unexpected extra argument: {token}")
        reasoning_model = token
        i += 1

    return provider, reasoning_model, toolcall_model


def _cmd_model(session: Session, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        # User-typed bare ``/model`` reserves exclusive stdin and opens the
        # picker. Agent ``slash_invoke`` runs without that reservation — opening
        # the picker against the live prompt races CPR into the composer and
        # stalls SessionGoal turns (shell-load dogfood H3). Show settings
        # instead; interactive switch stays on typed ``/model`` or ``/model set``.
        from core.agent_harness.spi.session_state import exclusive_stdin_active

        if exclusive_stdin_active(session):
            return _interactive_model_menu(session, console)
        render_current_models(console)
        return True

    sub = (args[0].lower() if args else "show").strip()

    if sub == "show":
        render_current_models(console)
        return True

    if sub == "toolcall":
        if len(args) >= 2 and args[1].lower() == "show":
            render_current_models(console)
            return True
        if len(args) >= 2 and args[1].lower() in ("set", "use", "switch"):
            if len(args) < 3:
                console.print(f"[{DIM}]usage:[/] /model toolcall set <model>")
                return True
            switch_toolcall_model(args[2], console)
            return True
        console.print(
            f"[{DIM}]usage:[/] /model toolcall set <model> "
            f"[{DIM}](sets the toolcall model for the active provider)[/]"
        )
        return True

    if sub in ("restore", "default", "reset"):
        if len(args) > 2:
            console.print(f"[{DIM}]usage:[/] /model restore [provider]")
            session.mark_latest(ok=False, kind="slash")
            return True
        provider_name = args[1] if len(args) == 2 else os.getenv(LLM_PROVIDER_ENV, "anthropic")
        restored = restore_default_model(provider_name, console)
        if not restored:
            session.mark_latest(ok=False, kind="slash")
        return True

    if sub in ("set", "use", "switch"):
        try:
            provider_name, reasoning_model, tc_model = parse_model_set_args(args[1:])
        except ValueError as exc:
            console.print()
            console.print(f"[{ERROR}]{escape(str(exc))}[/]")
            console.print()
            console.print(
                f"[{DIM}]usage:[/] /model set <provider> [model] [--toolcall-model <model>]"
            )
            session.mark_latest(ok=False, kind="slash")
            return True
        from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

        if provider_name.strip().lower() not in PROVIDER_BY_VALUE:
            if tc_model is not None:
                console.print()
                console.print(f"[{ERROR}]--toolcall-model requires an explicit provider[/]")
                console.print()
                console.print(
                    f"[{DIM}]usage:[/] /model set <provider> [model] [--toolcall-model <model>]"
                )
                session.mark_latest(ok=False, kind="slash")
                return True
            model_value = (
                provider_name if reasoning_model is None else f"{provider_name}-{reasoning_model}"
            )
            switched = switch_reasoning_model(model_value, console)
            if not switched:
                session.mark_latest(ok=False, kind="slash")
            return True
        switched = switch_llm_provider(
            provider_name,
            console,
            model=reasoning_model,
            toolcall_model=tc_model,
        )
        if not switched:
            session.mark_latest(ok=False, kind="slash")
        return True

    console.print(
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/model show[/bold], "
        "[bold]/model set <provider> [model] [--toolcall-model <m>][/bold], "
        "[bold]/model restore [provider][/bold], "
        "or [bold]/model toolcall set <model>[/bold])"
    )
    return True


_MODEL_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("show", "show active provider and models"),
    ("set", "switch provider  ·  /model set <provider> [model]"),
    ("restore", "restore the active provider's default reasoning model"),
    ("toolcall", "manage toolcall model for the active provider"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/model",
        "Show or change active LLM settings.",
        _cmd_model,
        usage=(
            "/model",
            "/model show",
            "/model set <provider> [model] [--toolcall-model <model>]",
            "/model restore [provider]",
            "/model toolcall set <model>",
        ),
        notes=(
            "Typed bare /model opens an interactive menu; "
            "agent slash_invoke /model shows current settings.",
            "The menu stays open after show actions and closes after set, restore, or toolcall changes.",
        ),
        first_arg_completions=_MODEL_FIRST_ARGS,
    ),
]
