"""Messaging platform and identity fields for the shared editable form."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import click
from prompt_toolkit.application import Application

from config.command_inputs import split_input_option
from integrations.messaging_security import MessagingPlatform
from surfaces.shared.messaging_options import messaging_parameters, validate_allow_user_id
from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    InputFormError,
    build_input_form,
)


def parse_messaging_draft(args: list[str]) -> dict[str, str]:
    """Keep supplied values unchanged while rejecting malformed option syntax."""
    command = click.Command(args[0], params=messaging_parameters(args[0]))
    by_flag = {
        flag: option
        for option in command.params
        if isinstance(option, click.Option)
        for flag in option.opts
    }
    values = {option.name: "" for option in command.params if option.name}
    index = 1
    while index < len(args):
        flag, value = split_input_option(args[index], by_flag)
        option = by_flag.get(flag)
        if option is None:
            raise click.UsageError(f"Unknown option: {flag}")
        if value is None:
            index += 1
            if index >= len(args):
                raise click.UsageError(f"{flag} requires a value.")
            value = args[index]
        assert option.name is not None
        values[option.name] = value
        index += 1
    return values


def build_messaging_form(
    args: list[str], *, allowed_users: Callable[[str], Sequence[str]]
) -> Application[list[str] | None]:
    """Collect missing inputs; only explicit submission returns executable arguments."""
    action = args[0]
    command = click.Command(action, params=messaging_parameters(action))
    values = parse_messaging_draft(args)

    def fields(current: dict[str, str]) -> list[InputField]:
        result = [
            InputField(
                "platform",
                "Platform",
                description="Choose the inbound messaging platform.",
                status="required",
                required=True,
                choices=tuple(
                    InputChoice(p.value, p.value.capitalize()) for p in MessagingPlatform
                ),
                case_sensitive=False,
            )
        ]
        if action in {"allow", "revoke"}:
            users = allowed_users(current["platform"].lower()) if action == "revoke" else ()
            choices = tuple(InputChoice(user_id, user_id) for user_id in users)
            if action == "revoke" and not choices:
                choices = (InputChoice("", "No allowed users"),)
            result.append(
                InputField(
                    "user_id",
                    "User ID",
                    description=(
                        "No allowed users. Choose another platform or cancel."
                        if action == "revoke" and not users
                        else "Choose an allowed user. Revocation invalidates any pending pairing code."
                        if action == "revoke"
                        else "Use a platform-native user ID, not an @username."
                    ),
                    status="required",
                    required=True,
                    choices=choices,
                    searchable=action == "revoke",
                )
            )
        return result

    def validate(current: dict[str, str]) -> list[str]:
        arguments = [
            flag
            for name, value in current.items()
            for flag in (f"--{name.replace('_', '-')}", value)
        ]
        try:
            with command.make_context(f"/messaging {action}", arguments) as context:
                platform = context.params["platform"]
                user_id = context.params.get("user_id", "")
                if action == "allow":
                    user_id = validate_allow_user_id(platform, user_id)
                if action == "revoke" and user_id not in allowed_users(platform):
                    raise InputFormError(
                        "Choose an allowed user for this platform.", field="user_id"
                    )
        except click.ClickException as exc:
            param = exc.param.name if isinstance(exc, click.BadParameter) and exc.param else None
            raise InputFormError(exc.format_message(), field=param or "user_id") from exc
        result = [action, "--platform", platform]
        if action in {"allow", "revoke"}:
            result.extend(("--user-id", user_id))
        return result

    labels = {
        "status": "Show status",
        "pair": "Generate pairing code",
        "allow": "Allow user",
        "revoke": "Revoke access",
    }
    confirmations = {
        "pair": "Enable inbound messaging and replace any pending pairing code?",
        "allow": "Enable inbound messaging and allow this user without DM pairing?",
        "revoke": "Revoke this user's access and invalidate any pending pairing code?",
    }
    return build_input_form(
        values,
        title=f"/messaging {action}",
        heading=labels[action],
        submit_label=labels[action],
        fields=fields,
        validate=validate,
        confirmation=confirmations.get(action, ""),
    )
