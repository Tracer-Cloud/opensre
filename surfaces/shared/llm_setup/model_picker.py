"""Single-choice model picker shared by login, onboarding and model commands."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.text import Text

from config.constants.llm import HOSTED_MODEL_SOURCE_ID as HOSTED_SOURCE
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
from surfaces.shared.llm_setup.model_inventory import model_choices
from surfaces.shared.llm_setup.model_results import ModelChoice, ModelSelectionError
from surfaces.shared.llm_setup.selection import (
    SelectionResult,
    select_hosted_model,
    select_provider,
)
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.input_form import InputChoice, InputField, build_input_form

_CONNECT = "__connect__"
_CUSTOM = "__custom__"
_LATER = "__later__"


def _custom_model(provider_id: str | None, rows: tuple[ModelChoice, ...]) -> tuple[str, str] | None:
    providers = tuple(
        dict.fromkeys(
            row.provider_id
            for row in rows
            if row.provider_id in PROVIDER_BY_VALUE
            and PROVIDER_BY_VALUE[row.provider_id].allow_custom_models
        )
    )
    if provider_id:
        providers = tuple(p for p in providers if p == provider_id)
    if not providers:
        return None
    fields = (
        InputField(
            "provider",
            "Provider / source",
            required=True,
            searchable=True,
            choices=tuple(
                InputChoice(p, next(r.source for r in rows if r.provider_id == p))
                for p in providers
            ),
        ),
        InputField(
            "model",
            "Exact model ID",
            required=True,
            description="Required. Use the exact ID served by this provider.",
        ),
    )
    app = build_input_form(
        {"provider": providers[0], "model": ""},
        title="Custom model",
        heading="Choose a provider and enter a model ID",
        submit_label="Use model",
        fields=lambda _: fields,
        validate=lambda values: (values["provider"], values["model"].strip()),
        initial_field="model" if len(providers) == 1 else "provider",
    )
    return run_command_input(app)


def model_picker(
    console: Console,
    *,
    provider_id: str | None = None,
    preferred_model: str | None = None,
    connect: Callable[[str | None], SelectionResult | None] | None = None,
) -> SelectionResult | None:
    """Choosing a model is the only gesture that activates it; Esc keeps settings."""
    while True:
        rows = model_choices(provider_id=provider_id)
        choices = [
            InputChoice(
                row.key,
                row.label,
                summary=row.source + (" · current" if row.active else ""),
                details=((row.detail,) if row.detail else ()),
                search_text=f"{row.provider_id} {row.model_id} {row.source}",
                summary_style="description",
            )
            for row in rows
        ]
        if any(
            row.provider_id in PROVIDER_BY_VALUE
            and PROVIDER_BY_VALUE[row.provider_id].allow_custom_models
            and row.availability != "reconnect"
            for row in rows
        ):
            choices.append(InputChoice(_CUSTOM, "Custom model…"))
        if provider_id:
            choices.append(InputChoice(_LATER, "Choose later, keep current model"))
        else:
            choices.append(InputChoice(_CONNECT, "+ Connect a provider"))
        preferred = next(
            (
                row
                for row in rows
                if preferred_model is not None
                and row.model_id == preferred_model
                and row.availability != "reconnect"
            ),
            None,
        )
        current = next((row for row in rows if row.active), None)
        default = next(
            (
                row
                for row in rows
                if row.provider_id in PROVIDER_BY_VALUE
                and row.model_id == PROVIDER_BY_VALUE[row.provider_id].default_model
                and row.availability != "reconnect"
            ),
            None,
        )
        initial = preferred or current or default or next(iter(rows), None)
        description = (
            f"Current: {current.label} · {current.source}"
            if current
            else (
                "No providers connected"
                if not rows
                else "Select a model to activate its connection"
            )
        )

        def fields(
            _values: dict[str, str],
            choices: tuple[InputChoice, ...] = tuple(choices),
            has_rows: bool = any(row.availability != "reconnect" for row in rows),
        ) -> tuple[InputField, ...]:
            return (
                InputField(
                    "model",
                    "Models" if has_rows else "No providers connected",
                    required=True,
                    choices=choices,
                    searchable=True,
                    record_choices=True,
                ),
            )

        app = build_input_form(
            {"model": initial.key if initial else choices[0].value or ""},
            title="Choose a model",
            heading=description,
            submit_label="Use model",
            fields=fields,
            validate=lambda values: values["model"],
            initial_field="model",
            submit_on_choice=True,
        )
        selected = run_command_input(app)
        if selected is None or selected == _LATER:
            return None
        if selected == _CONNECT:
            result = connect(None) if connect else None
            if result:
                return result
            continue
        try:
            if selected == _CUSTOM:
                custom = _custom_model(provider_id, rows)
                if custom is None:
                    continue
                result = select_provider(*custom)
                source = next(row.source for row in rows if row.provider_id == custom[0])
                label = custom[1]
            else:
                row = next(row for row in rows if row.key == selected)
                if row.availability == "reconnect":
                    console.print(Text(row.detail))
                    if connect and row.provider_id != HOSTED_SOURCE:
                        result = connect(row.provider_id)
                        if result:
                            return result
                    else:
                        return None
                    continue
                result = (
                    select_hosted_model(row.model_id)
                    if row.provider_id == HOSTED_SOURCE
                    else select_provider(row.provider_id, row.model_id)
                )
                source, label = row.source, row.label
        except (ModelSelectionError, RuntimeError, OSError) as exc:
            console.print(Text(str(exc)))
            continue
        console.print(Text(f"Now using {label} · {source}"))
        return result
