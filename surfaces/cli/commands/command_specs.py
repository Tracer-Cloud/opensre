"""Top-level CLI command index: name, help, and import path.

Root ``opensre --help`` reads this table and must not import command
modules. ``get_command`` loads one module when that command actually runs,
so ``opensre ask`` / ``opensre account login`` still execute the real Click
objects (options, groups, callbacks).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

import click


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One top-level ``opensre`` command, independent of its implementation module."""

    name: str
    short_help: str
    import_path: str
    hidden: bool = False


# Help strings are the first line of each command's docstring. A test loads
# the real Click object and fails if these drift.
COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(
        "account",
        "Sign in to OpenSRE with GitHub and inspect the local account.",
        "surfaces.cli.commands.account:account_command",
    ),
    CommandSpec(
        "ask",
        "Run one configured OpenSRE agent request and exit.",
        "surfaces.cli.commands.ask:ask_command",
    ),
    CommandSpec(
        "setup",
        "Sign in with GitHub, activate the OpenSRE-hosted LLM, then open the shell.",
        "surfaces.cli.commands.setup:setup_command",
    ),
    CommandSpec(
        "onboard",
        "Run the interactive onboarding wizard.",
        "surfaces.cli.commands.onboard:onboard",
    ),
    CommandSpec(
        "auth",
        "Log in to LLM providers and inspect local auth state.",
        "surfaces.cli.commands.auth:auth_command",
    ),
    CommandSpec(
        "config",
        "LLM/environment config by default; subcommands manage ~/.opensre/config.yml.",
        "surfaces.cli.commands.config:config_command",
    ),
    CommandSpec(
        "integrations",
        "Manage local integration credentials.",
        "surfaces.cli.commands.integrations:integrations",
    ),
    CommandSpec(
        "guardrails",
        "Manage sensitive information guardrail rules.",
        "surfaces.cli.commands.guardrails:guardrails",
    ),
    CommandSpec(
        "fleet",
        "Manage the local AI agent fleet (Claude Code, Cursor, Aider, ...).",
        "surfaces.cli.commands.agent:fleet",
    ),
    CommandSpec(
        "messaging",
        "Messaging security: DM pairing and identity management.",
        "surfaces.cli.commands.messaging:messaging",
    ),
    CommandSpec(
        "cron",
        "Manage cron-driven scheduled deliveries to messaging providers.",
        "surfaces.cli.commands.cron:cron_command",
    ),
    CommandSpec(
        "sentry",
        "Sentry-specific automation and digests.",
        "surfaces.cli.commands.sentry_digest:sentry_command",
    ),
    CommandSpec(
        "posthog",
        "PostHog-specific automation and reports.",
        "surfaces.cli.commands.posthog_report:posthog_command",
    ),
    CommandSpec(
        "work",
        "Manage durable human tasks, reminders, and prioritization.",
        "surfaces.cli.commands.work:work_command",
    ),
    CommandSpec(
        "debug",
        "Run targeted debug checks.",
        "surfaces.cli.commands.debug:debug_command",
    ),
    CommandSpec(
        "gateway",
        "Run the OpenSRE gateway daemon (web app, Telegram chat, task scheduler).",
        "surfaces.cli.commands.gateway:gateway_command",
    ),
    CommandSpec(
        "remote-sync",
        "Mirror sessions and memory to your own object store.",
        "surfaces.cli.commands.remote_sync:remote_sync_command",
    ),
    CommandSpec(
        "health",
        "Show a quick health summary of the local agent setup.",
        "surfaces.cli.commands.general:health_command",
    ),
    CommandSpec(
        "doctor",
        "Run a full environment diagnostic to surface setup issues.",
        "surfaces.cli.commands.doctor:doctor_command",
    ),
    CommandSpec(
        "update",
        "Check for a newer main build and update if one is available.",
        "surfaces.cli.commands.general:update_command",
    ),
    CommandSpec(
        "uninstall",
        "Remove opensre and all local data from this machine.",
        "surfaces.cli.commands.general:uninstall_command",
    ),
    CommandSpec(
        "version",
        "Print detailed version, Python and OS info.",
        "surfaces.cli.commands.general:version_command",
    ),
    CommandSpec(
        "_package-smoke",
        "Fail unless essential dynamically bundled code and data are available.",
        "surfaces.cli.commands.package_smoke:package_smoke_command",
        hidden=True,
    ),
)

COMMAND_SPECS_BY_NAME: dict[str, CommandSpec] = {spec.name: spec for spec in COMMAND_SPECS}


def visible_help_rows() -> tuple[tuple[str, str], ...]:
    """Name and short help for root ``opensre --help``, without importing commands."""
    return tuple((spec.name, spec.short_help) for spec in COMMAND_SPECS if not spec.hidden)


def load_command(spec: CommandSpec) -> click.Command:
    """Import one command module and return its Click object."""
    module_name, attr = spec.import_path.split(":", 1)
    command = getattr(importlib.import_module(module_name), attr)
    if not isinstance(command, click.Command):
        raise TypeError(f"{spec.import_path} is not a click.Command")
    return command


__all__ = [
    "COMMAND_SPECS",
    "COMMAND_SPECS_BY_NAME",
    "CommandSpec",
    "load_command",
    "visible_help_rows",
]
