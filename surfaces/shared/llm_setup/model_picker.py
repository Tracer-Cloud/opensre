"""One model picker for connected-provider selection and post-login handoff."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from infrastructure.terminal.theme import ERROR
from surfaces.shared.llm_setup.auth_results import SelectionError, SelectionResult
from surfaces.shared.llm_setup.model_choices import ModelChoice, connected_choices
from surfaces.shared.llm_setup.selection import select_provider
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    InputFormError,
    build_input_form,
)
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def render_models(console: Console, *, provider: str | None = None) -> None:
    """Render the same source-labelled choices used by the interactive picker."""
    rows = connected_choices(provider=provider)
    console.print(
        RecordTable(
            title="Connected models",
            columns=tuple(RecordColumn(name) for name in ("Model", "Source", "State")),
            rows=tuple(
                RecordRow(
                    cells=(
                        Text(row.label),
                        Text(row.source),
                        Text("current" if row.active else row.availability),
                    )
                )
                for row in rows
            ),
            caption="Connect a provider with /login or opensre auth login.",
        )
    )


def choose_model(
    console: Console,
    *,
    provider: str | None = None,
    connect: Callable[[], str | None] | None = None,
    initial_model: str | None = None,
) -> SelectionResult | None:
    """Select in one gesture; cancelled selection never changes the active route."""
    while True:
        rows = connected_choices(provider=provider)
        from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

        option = PROVIDER_BY_VALUE.get(provider or "")
        if (
            initial_model
            and option
            and option.allow_custom_models
            and rows
            and initial_model not in {row.model for row in rows}
        ):
            rows = [ModelChoice(option.value, initial_model, initial_model, rows[0].source), *rows]
        by_key = {row.key: row for row in rows}
        choices = [
            InputChoice(
                row.key,
                row.label,
                summary=row.source,
                summary_style="description",
                details=(row.availability,),
                search_text=f"{row.provider} {row.model} {row.source}",
                record=RecordRow(
                    cells=(
                        Text(row.label),
                        Text(row.source),
                        Text("current" if row.active else row.availability),
                    )
                ),
            )
            for row in rows
        ]
        if connect is not None:
            choices.append(InputChoice("__connect__", "+ Connect a provider"))
        if any(
            row.provider in PROVIDER_BY_VALUE
            and PROVIDER_BY_VALUE[row.provider].allow_custom_models
            for row in rows
        ):
            choices.append(InputChoice("__custom__", "Custom model…"))
        if provider is not None:
            choices.append(InputChoice("__later__", "Choose later, keep current model"))
        action_details = {
            "__connect__": ("Provider setup", "Connect"),
            "__custom__": ("Connected provider", "Enter model ID"),
            "__later__": ("Current model", "No change"),
        }
        choices = [
            choice
            if choice.record is not None
            else replace(
                choice,
                record=RecordRow(
                    cells=(
                        Text(choice.label),
                        *(Text(value) for value in action_details[choice.value or ""]),
                    )
                ),
            )
            for choice in choices
        ]
        if not choices:
            console.print("No providers connected. Run /login or opensre auth login.")
            return None
        current = next((row.key for row in rows if row.active), None)
        preferred = next(
            (row.key for row in rows if initial_model is not None and row.model == initial_model),
            None,
        )
        values = {"model": preferred or current or choices[0].value or ""}

        def validate(draft: dict[str, str], choices: list[InputChoice] = choices) -> str:
            value = draft["model"]
            if value not in {choice.value for choice in choices}:
                raise InputFormError("Choose a listed model.", field="model")
            return value

        def fields(
            _draft: dict[str, str],
            choices: list[InputChoice] = choices,
            has_rows: bool = bool(rows),
        ) -> list[InputField]:
            return [
                InputField(
                    "model",
                    "Model",
                    description=(
                        "Source and billing are shown with each model. Esc keeps your current model."
                        if has_rows
                        else "No providers connected. Choose Connect a provider to get started."
                    ),
                    required=True,
                    searchable=True,
                    choices=tuple(choices),
                    record_choices=True,
                    record_columns=tuple(
                        RecordColumn(name) for name in ("Model", "Source", "State")
                    ),
                )
            ]

        app = build_input_form(
            values,
            title="Choose a model",
            heading="Connected providers" if provider is None else f"Connected to {provider}",
            submit_label="Use model",
            fields=fields,
            validate=validate,
            initial_field="model",
            submit_on_choice=True,
        )
        picked = run_command_input(app)
        if picked in (None, "__later__"):
            return None
        if picked == "__connect__":
            if connect is not None:
                connected = connect()
                if connected is not None:
                    selected = choose_model(console, provider=connected)
                    if selected is not None:
                        return selected
            continue
        if picked == "__custom__":
            result = _custom_model(console, provider)
            if result is not None:
                return result
            continue
        row = by_key[picked]
        try:
            result = select_provider(row.provider, row.model)
        except KeyboardInterrupt:
            console.print("Model selection cancelled. Active model unchanged.")
            return None
        except (SelectionError, RuntimeError, OSError) as exc:
            console.print(f"[{ERROR}]{escape(str(exc))}[/]")
            continue
        console.print(Text(f"Now using {row.label} · {row.source}"))
        return result


def _custom_model(console: Console, provider: str | None) -> SelectionResult | None:
    """Collect an exact custom-model target using the existing editable form."""
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    providers = tuple(
        dict.fromkeys(
            row.provider
            for row in connected_choices(provider=provider)
            if row.provider in PROVIDER_BY_VALUE
            and PROVIDER_BY_VALUE[row.provider].allow_custom_models
        )
    )
    if not providers:
        console.print("These providers do not accept custom model IDs.")
        return None

    def validate(draft: dict[str, str]) -> tuple[str, str]:
        if draft["provider"] not in providers or not draft["model"].strip():
            raise InputFormError("Choose a connected provider and enter a model ID.")
        return draft["provider"], draft["model"]

    values = {"provider": providers[0], "model": ""}
    while True:
        target = run_command_input(
            build_input_form(
                values,
                title="Custom model",
                heading="Exact provider model ID",
                submit_label="Use model",
                fields=lambda _: [
                    InputField(
                        "provider",
                        "Provider",
                        required=True,
                        choices=tuple(
                            InputChoice(value, PROVIDER_BY_VALUE[value].label)
                            for value in providers
                        ),
                    ),
                    InputField("model", "Model ID", required=True),
                ],
                validate=validate,
            )
        )
        if target is None:
            return None
        try:
            result = select_provider(*target)
        except KeyboardInterrupt:
            console.print("Model selection cancelled. Active model unchanged.")
            return None
        except (SelectionError, RuntimeError, OSError) as exc:
            console.print(Text(str(exc)))
            values = {"provider": target[0], "model": target[1]}
            continue
        console.print(Text(f"Now using {result.model} · {result.provider}"))
        return result
