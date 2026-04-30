"""Rich landing and help renderers for the OpenSRE CLI."""

from __future__ import annotations

from collections.abc import Sequence

import click
from rich.console import Console
from rich.text import Text

_ASCII_HEADER = """\
  ___  ____  _____ _   _ ____  ____  _____
 / _ \\|  _ \\| ____| \\ | / ___||  _ \\| ____|
| | | | |_) |  _| |  \\| \\___ \\| |_) |  _|
| |_| |  __/| |___| |\\  |___) |  _ <| |___
 \\___/|_|   |_____|_| \\_|____/|_| \\_\\_____|"""

_HELP_COMMANDS: tuple[tuple[str, str], ...] = (
    ("agent", "Launch the interactive SRE agent terminal."),
    ("onboard", "Run the interactive onboarding wizard."),
    ("investigate", "Run an RCA investigation against an alert payload."),
    ("deploy", "Deploy OpenSRE to a cloud environment (EC2)."),
    ("remote", "Connect to remote agents and hosted service ops."),
    ("tests", "Browse and run inventoried tests from the terminal."),
    ("integrations", "Manage local integration credentials."),
    ("guardrails", "Manage sensitive information guardrail rules."),
    ("health", "Check integration and agent setup status."),
    ("doctor", "Run a full environment diagnostic."),
    ("update", "Check for a newer version and update if one is available."),
    ("version", "Print detailed version, Python and OS info."),
)

_LANDING_COMMANDS: tuple[tuple[str, str], ...] = (
    ("opensre agent", "Launch the interactive SRE agent terminal"),
    ("opensre onboard", "Configure LLM provider and integrations"),
    ("opensre investigate -i alert.json", "Run RCA against an alert payload"),
    ("opensre deploy ec2", "Deploy investigation server on AWS EC2"),
    ("opensre remote --url <ip> health", "Check a remote deployed agent"),
    ("opensre remote ops status", "Inspect hosted service status (Railway)"),
    ("opensre tests", "Browse and run inventoried tests"),
    ("opensre integrations list", "Show configured integrations"),
    ("opensre guardrails rules", "List configured guardrail rules"),
    ("opensre health", "Check integration and agent setup status"),
    ("opensre doctor", "Run a full environment diagnostic"),
    ("opensre update", "Update to the latest version"),
    ("opensre version", "Print detailed version, Python and OS info"),
)

_SHORT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("--json, -j", "Emit machine-readable JSON output."),
    ("--verbose", "Print extra diagnostic information."),
    ("--debug", "Print debug-level logs and traces."),
    ("--yes, -y", "Auto-confirm all interactive prompts."),
    ("--version", "Show the version and exit."),
    ("-h, --help", "Show this message and exit."),
)


def _render_usage(console: Console) -> None:
    console.print(
        Text.assemble(("  Usage: "), ("opensre", "bold white"), (" [OPTIONS] COMMAND [ARGS]..."))
    )


def _render_rows(
    console: Console,
    *,
    title: str,
    rows: Sequence[tuple[str, str]],
    width: int,
) -> None:
    console.print(Text.assemble((f"  {title}:", "bold white")))
    for label, description in rows:
        console.print(Text.assemble(("    ", ""), (f"{label:<{width}}", "bold cyan"), description))


def render_help() -> None:
    """Render the root help view."""
    console = Console(highlight=False)
    console.print()
    _render_usage(console)
    console.print()
    _render_rows(console, title="Commands", rows=_HELP_COMMANDS, width=16)
    console.print()
    _render_rows(console, title="Options", rows=_SHORT_OPTIONS, width=16)
    console.print()


def render_landing() -> None:
    """Render the root landing page shown with no subcommand."""
    console = Console(highlight=False)
    console.print()
    for line in _ASCII_HEADER.splitlines():
        console.print(Text.assemble(("  ", ""), (line, "bold cyan")))
    console.print()
    console.print(
        Text.assemble(
            ("  ", ""),
            "open-source SRE agent for automated incident investigation and root cause analysis",
        )
    )
    console.print()
    _render_usage(console)
    console.print()
    _render_rows(console, title="Quick start", rows=_LANDING_COMMANDS, width=42)
    console.print()
    _render_rows(console, title="Options", rows=_SHORT_OPTIONS, width=42)
    console.print()


class RichGroup(click.Group):
    """Click group with a custom Rich-powered help screen."""

    def format_help(self, _ctx: click.Context, _formatter: click.HelpFormatter) -> None:
        render_help()
