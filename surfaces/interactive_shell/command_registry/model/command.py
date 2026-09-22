"""Slash command /model and interactive provider/model menus."""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

from config.constants.llm import LLM_PROVIDER_ENV
from surfaces.interactive_shell.command_registry.model.presentation import (
    current_model_selection,
    render_current_models,
)
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
    return [
        *((provider.value, provider.label) for provider in focused_setup_provider_options()),
        (OTHER_PROVIDER_SELECTION, "Other providers ›"),
    ]


def _other_provider_menu_choices() -> list[tuple[str, str]]:
    return [(provider.value, provider.label) for provider in other_setup_provider_options()]


def _choose_provider_value(
    *, title: str, breadcrumb: str, initial_value: str | None = None
) -> str | None:
    current_provider, _, _ = current_model_selection()
    choices = _provider_menu_choices()
    featured = {value for value, _ in choices}
    current_bucket = (
        current_provider
        if current_provider in featured
        else (OTHER_PROVIDER_SELECTION if current_provider else None)
    )
    initial = initial_value or current_provider
    show_other = initial_value is not None and initial_value not in featured
    while True:
        if not show_other:
            provider_value = repl_choose_one(
                title=title,
                breadcrumb=breadcrumb,
                choices=choices,
                panel=True,
                initial_value=initial if initial in featured else OTHER_PROVIDER_SELECTION,
                current_value=current_bucket,
            )
            if provider_value != OTHER_PROVIDER_SELECTION:
                return provider_value
        provider_value = repl_choose_one(
            title="Other providers",
            breadcrumb=f"{breadcrumb}{CRUMB_SEP}Other providers",
            choices=_other_provider_menu_choices(),
            panel=True,
            initial_value=initial,
            current_value=current_provider,
        )
        if provider_value is not None:
            return provider_value
        initial = OTHER_PROVIDER_SELECTION
        show_other = False


def _reasoning_model_menu_choices(provider: object) -> list[tuple[str, str]]:
    model_options = list(getattr(provider, "models", ()))
    choices: list[tuple[str, str]] = [
        ("__provider_default__", "Use provider default"),
    ]
    for option in model_options:
        value = str(getattr(option, "value", ""))
        display = value if value else "cli-default"
        choices.append((value, display))
    if _provider_allows_custom_models(provider):
        choices.append(("__custom__", "Enter a custom model ID…"))
    return choices


def _toolcall_model_menu_choices(provider: object) -> list[tuple[str, str]]:
    model_options = list(getattr(provider, "models", ()))
    choices: list[tuple[str, str]] = [
        ("__keep__", "Keep current tool-call model"),
        ("__match_reasoning__", "Use the reasoning model"),
    ]
    for option in model_options:
        value = str(getattr(option, "value", ""))
        display = value if value else "cli-default"
        choices.append((value, display))
    if _provider_allows_custom_models(provider):
        choices.append(("__custom__", "Enter a custom model ID…"))
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

    crumb_set = f"{_ROOT}{CRUMB_SEP}Change model"
    current_provider, current_reasoning, current_toolcall = current_model_selection()
    provider_value: str | None = None
    while True:
        provider_value = _choose_provider_value(
            title="LLM provider",
            breadcrumb=crumb_set,
            initial_value=provider_value,
        )
        if provider_value is None:
            return None
        provider = PROVIDER_BY_VALUE.get(provider_value)
        if provider is None:
            return False

        crumb_model = f"{crumb_set}{CRUMB_SEP}{provider_value}"
        active_reasoning = current_reasoning if provider_value == current_provider else None
        active_toolcall = current_toolcall if provider_value == current_provider else None
        reasoning_initial = active_reasoning
        while True:
            from surfaces.interactive_shell.command_registry.model.provider_models import (
                model_menu_choices,
            )

            reasoning_choices = model_menu_choices(
                provider,
                console,
                fallback=_reasoning_model_menu_choices(provider),
            )
            if reasoning_choices is None:
                break
            reasoning_choice = repl_choose_one(
                title="Reasoning model",
                breadcrumb=crumb_model,
                choices=reasoning_choices,
                panel=True,
                initial_value=reasoning_initial,
                current_value=active_reasoning,
            )
            if reasoning_choice is None:
                break
            reasoning_initial = reasoning_choice

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
                        title="Tool-call model",
                        breadcrumb=crumb_tc,
                        choices=_toolcall_model_menu_choices(provider),
                        panel=True,
                        initial_value=active_toolcall,
                        current_value=active_toolcall,
                    )
                    if toolcall_value is None:
                        break
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
                if toolcall_value is None:
                    continue

            return switch_llm_provider(
                provider.value,
                console,
                model=model_choice,
                toolcall_model=toolcall_model,
            )


def _interactive_restore_provider(console: Console) -> bool | None:
    provider_value = _choose_provider_value(
        title="LLM provider",
        breadcrumb=f"{_ROOT}{CRUMB_SEP}Restore defaults",
    )
    if provider_value is None:
        return None
    return restore_default_model(provider_value, console)


def _interactive_set_toolcall(console: Console) -> bool | None:
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    crumb_tc = f"{_ROOT}{CRUMB_SEP}Tool-call model"
    current_provider, _, current_toolcall = current_model_selection()
    provider_value: str | None = None
    while True:
        provider_value = _choose_provider_value(
            title="LLM provider", breadcrumb=crumb_tc, initial_value=provider_value
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
        active = current_toolcall if provider_value == current_provider else None
        initial = active
        while True:
            model_value = repl_choose_one(
                title="Tool-call model",
                breadcrumb=f"{crumb_tc}{CRUMB_SEP}{provider_value}",
                choices=_toolcall_model_menu_choices(provider),
                panel=True,
                initial_value=initial,
                current_value=active,
            )
            if model_value is None:
                break
            initial = model_value
            if model_value == "__keep__":
                console.print("[dim]toolcall model left unchanged.[/dim]")
                return True
            if model_value == "__match_reasoning__":
                reasoning = (
                    os.getenv(provider.model_env, "") or ""
                ).strip() or provider.default_model
                return switch_toolcall_model(reasoning, console, provider_name=provider.value)
            if model_value == "__custom__":
                custom_tc = _prompt_custom_model_id(console, provider.value)
                if custom_tc is None:
                    continue
                model_value = custom_tc
            return switch_toolcall_model(str(model_value), console, provider_name=provider.value)


def _interactive_model_menu(session: Session, console: Console) -> bool:
    initial = "set"
    while True:
        provider, reasoning, _ = current_model_selection()
        action = repl_choose_one(
            title="Model",
            panel=True,
            initial_value=initial,
            note=f"Current: {provider} · {reasoning}" if provider else "No model configured",
            breadcrumb=f"{_ROOT}",
            choices=[
                ("set", "Change model ›"),
                ("show", "View configuration"),
                ("toolcall", "Configure tool-call model ›"),
                ("restore", "Restore provider defaults ›"),
                ("done", "Done"),
            ],
        )
        if action is None or action == "done":
            return True
        initial = action
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
