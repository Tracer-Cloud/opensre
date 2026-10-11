"""Goal condition fields and validation for the shared editable command form."""

from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.application import Application

from core.agent_harness.spi.session_goal import SESSION_GOAL_UNBOUNDED_TURNS
from surfaces.shared.terminal.components.input_form import (
    InputField,
    InputFormError,
    build_input_form,
)

_FIELDS = ("condition", "max_turns")


@dataclass(frozen=True)
class GoalSetValues:
    """Validated ``/goal set`` draft: condition plus turn budget (0 == unbounded)."""

    condition: str
    max_turns: int


def parse_max_turns(raw: str, *, allow_blank: bool = True) -> int:
    """Parse a ``--max-turns`` value; blank means unbounded when allowed.

    Raise ValueError when the value is not an integer — and always for a blank
    value when ``allow_blank`` is False, so direct commands keep rejecting an
    explicitly supplied empty budget instead of starting unbounded work.
    Non-positive integers clamp to a single turn, matching direct-command
    semantics.
    """
    text = raw.strip()
    if not text:
        if allow_blank:
            return SESSION_GOAL_UNBOUNDED_TURNS
        raise ValueError(f"invalid --max-turns value: {raw!r}")
    return max(1, int(text))


def _validate_condition(value: str) -> None:
    if not value:
        raise InputFormError("Enter a completion condition.", field="condition")


def _validate_max_turns(value: str) -> None:
    try:
        parse_max_turns(value)
    except ValueError as exc:
        raise InputFormError(
            "Enter a positive integer for max turns, or leave empty for unbounded.",
            field="max_turns",
        ) from exc


def _validate(values: dict[str, str]) -> GoalSetValues:
    condition = values["condition"].strip()
    if not condition:
        raise InputFormError("Enter a completion condition.", field="condition")
    try:
        max_turns = parse_max_turns(values["max_turns"])
    except ValueError as exc:
        raise InputFormError(
            "Enter a positive integer for max turns, or leave empty for unbounded.",
            field="max_turns",
        ) from exc
    return GoalSetValues(condition=condition, max_turns=max_turns)


def build_goal_set_form(options: dict[str, str]) -> Application[GoalSetValues | None]:
    """Collect a condition, then confirm; attach only after explicit Set goal."""
    current = {name: options.get(name, "") for name in _FIELDS}

    def fields(_values: dict[str, str]) -> list[InputField]:
        return [
            InputField(
                "condition",
                "Condition",
                description=(
                    "Confirming attaches the goal and runs this condition as the next turn."
                ),
                status="required",
                required=True,
                validate=_validate_condition,
                cancel_on_back_if_empty=True,
            ),
            InputField(
                "max_turns",
                "Max turns",
                description="Optional · positive integer; empty leaves the goal unbounded.",
                status="default Unbounded",
                validate=_validate_max_turns,
            ),
        ]

    return build_input_form(
        current,
        title="/goal set",
        heading="New goal",
        submit_label="Set goal",
        fields=fields,
        validate=_validate,
        initial_field="condition",
    )
