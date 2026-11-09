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
_GOAL_CONDITION_DESCRIPTION = "What should OpenSRE achieve?\nExample: Identify checkout 502s."
_MISSING_CONDITION_ERROR = "Add a goal condition to continue."


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


_MAX_TURNS_ERROR = "Enter a positive integer for max turns, or leave empty for unbounded."


def _validate_condition(value: str) -> None:
    if not value:
        raise InputFormError(_MISSING_CONDITION_ERROR, field="condition")


def _validate_max_turns(value: str) -> None:
    text = value.strip()
    if not text:
        return
    try:
        number = int(text)
    except ValueError as exc:
        raise InputFormError(_MAX_TURNS_ERROR, field="max_turns") from exc
    if number <= 0:
        raise InputFormError(_MAX_TURNS_ERROR, field="max_turns")


def _validate(values: dict[str, str]) -> GoalSetValues:
    condition = values["condition"].strip()
    if not condition:
        raise InputFormError(_MISSING_CONDITION_ERROR, field="condition")
    _validate_max_turns(values["max_turns"])
    return GoalSetValues(condition=condition, max_turns=parse_max_turns(values["max_turns"]))


def build_goal_set_form(options: dict[str, str]) -> Application[GoalSetValues | None]:
    """Collect a condition, then confirm; attach only after explicit Set goal."""
    current = {name: options.get(name, "") for name in _FIELDS}

    def fields(values: dict[str, str]) -> list[InputField]:
        return [
            InputField(
                "condition",
                "Condition",
                description=_GOAL_CONDITION_DESCRIPTION,
                status="required",
                required=True,
                validate=_validate_condition,
                cancel_on_back_if_empty=True,
            ),
            InputField(
                "max_turns",
                "Max turns",
                description="Optional · enter a positive integer, or leave blank for unbounded.",
                status="default Unbounded",
                summary=values["max_turns"] or "Unbounded",
                validate=_validate_max_turns,
            ),
        ]

    return build_input_form(
        current,
        title="/goal set",
        heading="Set a goal",
        submit_label="Set goal",
        fields=fields,
        validate=_validate,
        initial_field="condition",
    )


def build_goal_edit_form(condition: str, *, paused: bool) -> Application[str | None]:
    """Review the current condition before saving an edit to the attached goal."""

    def validate(values: dict[str, str]) -> str:
        value = values["condition"].strip()
        _validate_condition(value)
        return value

    return build_input_form(
        {"condition": condition},
        title="/goal edit",
        heading="Edit paused goal" if paused else "Edit active goal",
        submit_label="Save changes",
        fields=lambda _values: [
            InputField(
                "condition",
                "Condition",
                description=_GOAL_CONDITION_DESCRIPTION,
                status="required",
                required=True,
                validate=_validate_condition,
            )
        ],
        validate=validate,
    )
