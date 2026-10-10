"""Compact cron review fields and contextual editing copy."""

from __future__ import annotations

from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.interactive_shell.ui.cron_input.arguments import (
    cron_options,
    cron_template_defaults,
    required_cron_fields,
    visible_cron_fields,
)


def review_fields(values: dict[str, str]) -> list[str]:
    """Keep task intent, schedule, and delivery in the primary review."""
    required = set(required_cron_fields(values))
    visible = set(visible_cron_fields(values))
    names = ["kind"]
    if values["name"]:
        names.append("name")
    names.extend(
        name
        for name in ("template", "prompt", "skill_name")
        if name in visible and (values[name] or name in required)
    )
    names.extend(("cron_expr", "provider"))
    names.extend(name for name in ("chat_id", "owner", "repo") if values[name] or name in required)
    return names


def field_label(name: str) -> str:
    return {
        "kind": "Task type",
        "cron_expr": "Schedule",
        "timezone": "Timezone",
        "provider": "Delivery",
        "chat_id": "Destination",
        "skill_name": "Skill",
        "window_hours": "Lookback",
        "pr_number": "Pull request",
        "github_connection_id": "GitHub app",
    }.get(name, name.replace("_", " ").capitalize())


def choice_label(name: str, value: str) -> str:
    if not value:
        return "Use default / unset"
    if name == "stateless":
        return "On" if value == "True" else "Off"
    if name in {"kind", "provider"}:
        return {
            "github_pr_sweep": "GitHub pull requests",
            "posthog_metric_report": "PostHog metrics",
            "work_item_checkin": "Work item check-in",
            "rocketchat": "Rocket.Chat",
        }.get(value.lower(), value.replace("_", " ").capitalize())
    return value


def field_status(name: str, values: dict[str, str]) -> str:
    """Describe requirements and defaults only while editing a field."""
    if name in required_cron_fields(values):
        return "required"
    default = _field_default(name, values)
    if default:
        return f"default {choice_label(name, default)}"
    return "optional"


def field_help(name: str, values: dict[str, str]) -> str:
    if name == "cron_expr":
        help_text = "Example: 0 9 * * * (09:00 daily).\n5 fields; optional seconds first."
        if values["kind"].lower() == "manual_loop":
            help_text += "\nManual loops run at most hourly."
        return help_text
    return {
        "kind": "Choose what should run on a schedule.",
        "prompt": "What should this task do each time it runs?",
        "provider": "Choose where to deliver the results.",
        "chat_id": "Chat or channel ID. Leave empty to use the configured destination.",
        "template": "Use a shipped task template instead of a prompt.",
        "skill_name": "Skill name. Agent loops also accept an installed skill folder.",
        "mode": "Report returns a result; agent executes the task.",
        "stateless": "Start each agent run fresh, without earlier runs or memory.",
    }.get(name, cron_options()[name].help or "")


def summary_value(name: str, values: dict[str, str]) -> str:
    value = values[name] or _field_default(name, values)
    if not value:
        return "Required" if name in required_cron_fields(values) else "—"
    if name in {"kind", "provider", "stateless"}:
        value = choice_label(name, value)
    if name == "cron_expr":
        value += f" · {values['timezone'] or 'UTC'}"
    return " ".join(strip_terminal_controls(value).split())


def _field_default(name: str, values: dict[str, str]) -> str:
    inherited = cron_template_defaults(values["template"]).get(name)
    if inherited:
        return inherited
    option = cron_options()[name]
    if name == "mode":
        return "report"
    return "" if option.required or option.default is None else str(option.default)
