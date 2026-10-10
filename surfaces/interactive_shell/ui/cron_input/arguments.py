"""Cron form fields and argument conversion backed by the CLI contract."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from types import MappingProxyType

import click

from core.agent_harness import load_loop_template, normalize_skill_name
from infrastructure.scheduling.scheduler.credentials import requires_explicit_chat_id
from surfaces.shared.cron_options import cron_add_parameters
from surfaces.shared.cron_tasks import prepare_cron_task


@cache
def cron_options() -> Mapping[str, click.Option]:
    """Return the same option definitions used by fully specified CLI commands."""
    return MappingProxyType(
        {p.name: p for p in cron_add_parameters() if isinstance(p, click.Option) and p.name}
    )


def parse_cron_draft(args: list[str]) -> dict[str, str]:
    """Preserve raw values, including invalid edits; reject unknown or malformed flags."""
    options = cron_options()
    by_flag = {flag: option for option in options.values() for flag in option.opts}
    values = {
        name: "" if option.required or option.default is None else str(option.default)
        for name, option in options.items()
    }
    index = 0
    while index < len(args):
        flag, separator, value = args[index].partition("=")
        option = by_flag.get(flag)
        if option is None:
            raise click.UsageError(f"Unknown option: {flag}")
        if option.is_flag:
            if separator:
                raise click.UsageError(f"{flag} does not take a value.")
            value = "True"
        elif not separator:
            index += 1
            if index >= len(args):
                raise click.UsageError(f"{flag} requires a value.")
            value = args[index]
        assert option.name is not None
        values[option.name] = value
        index += 1
    return values


def missing_cron_fields(values: dict[str, str]) -> list[str]:
    """Find missing values, including kind-specific requirements and delivery defaults."""
    required = ["kind", "provider"]
    if not values["template"].strip():
        required.append("cron_expr")
    kind = values["kind"].lower()
    if kind == "manual_loop":
        if values["template"].strip():
            required.extend(("owner", "repo"))
        elif not (_effective_mode(values) == "agent" and values["skill_name"].strip()):
            required.append("prompt")
    if (
        kind == "manual_loop"
        and _effective_mode(values) == "agent"
        and any(values[name].strip() for name in ("owner", "repo", "branch", "pr_number"))
    ):
        required.extend(("owner", "repo"))
    if kind == "recurring_skill":
        required.append("skill_name")
        if normalize_skill_name(values["skill_name"]) == "reporting-github-ci-failures":
            required.extend(("owner", "repo"))
    if values["provider"] and requires_explicit_chat_id(values["provider"]):
        required.append("chat_id")
    return [field for field in required if not values[field].strip()]


def cron_add_needs_input(args: list[str]) -> bool:
    """Select only incomplete add commands; help, malformed and complete calls stay direct."""
    if not args or args[0].lower() != "add":
        return False
    try:
        values = parse_cron_draft(args[1:])
    except click.ClickException:
        return False
    return bool(missing_cron_fields(values))


def cron_arguments(values: dict[str, str]) -> list[str]:
    """Serialize edited values as individual argv tokens, without shell interpolation."""
    args: list[str] = []
    for name, option in cron_options().items():
        value = values[name]
        if option.is_flag:
            if value == "True":
                args.append(option.opts[0])
        elif value:
            args.extend((option.opts[0], value))
    return args


def validate_cron_draft(values: dict[str, str]) -> list[str]:
    """Apply Click types and shared task validation without persistence or execution."""
    args = cron_arguments(values)
    command = click.Command("/cron add", params=list(cron_options().values()))
    with command.make_context("/cron add", args) as context:
        prepare_cron_task(**context.params)
    return cron_arguments(values)


def visible_cron_fields(values: dict[str, str]) -> list[str]:
    """Show relevant options and retain supplied values even when their kind changes."""
    names = ["kind", "name", "description", "cron_expr", "timezone", "provider", "chat_id"]
    kind = values["kind"].lower()
    mode = _effective_mode(values)
    skill = normalize_skill_name(values["skill_name"])
    if kind == "manual_loop":
        names.extend(("template", "prompt", "mode"))
        if mode == "agent":
            names.extend(("skill_name", "stateless"))
        if mode == "agent" or values["template"]:
            names.extend(("owner", "repo"))
        if mode == "agent":
            names.extend(("branch", "pr_number"))
    if kind == "recurring_skill":
        names.append("skill_name")
        if skill == "reporting-github-ci-failures":
            names.extend(("owner", "repo", "branch", "pr_number", "github_connection_id"))
        elif skill == "delivering-morning-briefings":
            names.append("city")
    names.append("window_hours")
    seen = set(names)
    for name, option in cron_options().items():
        default = str(option.default) if option.default is not None else ""
        if name not in seen and values[name] and values[name] != default:
            names.append(name)
    return names


@cache
def cron_template_defaults(name: str) -> Mapping[str, str]:
    """Display only defaults actually applied by cron task preparation."""
    if not name:
        return MappingProxyType({})
    try:
        template = load_loop_template(name)
    except (ValueError, KeyError, RuntimeError):
        return MappingProxyType({})
    return MappingProxyType(
        {
            "name": template.name,
            "cron_expr": template.cron,
            "mode": template.mode or "report",
        }
    )


def _effective_mode(values: dict[str, str]) -> str:
    """Use an explicit mode before the template's default, as task preparation does."""
    return values["mode"].lower() or cron_template_defaults(values["template"]).get(
        "mode", "report"
    )
