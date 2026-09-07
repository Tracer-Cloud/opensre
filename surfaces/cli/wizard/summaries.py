"""Rendered summary screens for the wizard onboarding flow.

One job: print the wizard's non-interactive output sections — the opening
splash header, the post-onboarding saved-configuration summary, optional
integration result cards, and the closing next-steps list. These are pure
renders against the shared ``console`` (from
:mod:`surfaces.cli.wizard.components`); they hold no prompt or state logic.
"""

from __future__ import annotations

from typing import cast

from rich import box
from rich.align import Align
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from config.version import get_opensre_version
from infrastructure.terminal.theme import (
    BRAND,
    DIM,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
)
from surfaces.cli.wizard.components import console
from surfaces.cli.wizard.integration_health import IntegrationHealthResult
from surfaces.shared.terminal.banner import build_launch_banner


def render_header() -> None:
    """Print the onboarding splash using the design-system palette.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
        ___                    ____  ____  _____ [HIGHLIGHT art]
       / _ \\ ...
      opensre  ·  v<version>                     [SECONDARY name] [DIM ·] [BRAND version]
      open-source SRE agent for automated …      [SECONDARY description]
      ─────────────────────────────────────────  [DIM rule]
      Complete your setup to get started          [TEXT heading]
      [1] Select your LLM provider and key        [BRAND number] [SECONDARY body]
      [2] OpenSRE checks the connection           [BRAND number] [SECONDARY body]
    """
    _render_splash_header(
        heading="Complete your setup to get started",
        steps=(
            "Select your LLM provider and add its API key or CLI login.",
            "OpenSRE checks the connection and continues.",
        ),
    )


def render_factory_setup_header() -> None:
    """Print the shell launch banner followed by the two-step account setup."""
    console.print()
    console.print(build_launch_banner(console))

    steps = Text()
    steps.append("1  ", style=f"bold {BRAND}")
    steps.append("Sign in with GitHub", style=f"bold {TEXT}")
    steps.append(" in the OpenSRE webapp.", style=SECONDARY)
    steps.append("\n")
    steps.append("2  ", style=f"bold {BRAND}")
    steps.append("Start the shell", style=f"bold {TEXT}")
    steps.append(" with your hosted model.", style=SECONDARY)
    console.print(
        Align.center(
            Panel(
                steps,
                title="Setup · 2 steps",
                title_align="left",
                border_style=DIM,
                padding=(1, 2),
                expand=False,
                box=box.ROUNDED,
            )
        )
    )
    console.print()


def _render_splash_header(*, heading: str, steps: tuple[str, ...]) -> None:
    from surfaces.shared.terminal.components.banner_art import render_art

    art = render_art()
    version = get_opensre_version()

    console.print()
    console.print(Rule(style=DIM))
    console.print()

    for line in art.splitlines():
        t = Text()
        t.append("  ")
        t.append(line, style=f"bold {HIGHLIGHT}")
        console.print(t)

    console.print()

    subtitle = Text()
    subtitle.append("  ")
    subtitle.append("opensre", style=SECONDARY)
    subtitle.append("  ·  ", style=DIM)
    subtitle.append(f"v{version}", style=BRAND)
    console.print(subtitle)

    desc = Text()
    desc.append(
        "  open-source SRE agent for production operations and incident response",
        style=SECONDARY,
    )
    console.print(desc)
    console.print()
    console.print(Rule(style=DIM))
    console.print()

    setup = Text()
    setup.append(heading, style=f"bold {TEXT}")
    setup.append("\n\n")
    for index, body in enumerate(steps, start=1):
        setup.append(f"[{index}] ", style=f"bold {BRAND}")
        setup.append(body, style=SECONDARY)
        if index < len(steps):
            setup.append("\n")
    console.print(
        Panel(
            setup,
            border_style=DIM,
            padding=(1, 2),
            expand=True,
            box=box.ROUNDED,
        )
    )
    console.print()


def render_saved_summary(
    *,
    provider_label: str,
    model: str,
    saved_path: str,
    env_path: str,
    credential_line: str = "local credentials file (~/.opensre/credentials.json)",
) -> None:
    """Print the post-onboarding success screen.

    Rendered output (colour roles):
      ─────────────────────────────────────────  [DIM rule]
      ✓  Done.                                   [HIGHLIGHT ✓ + text]
      ─────────────────────────────────────────  [DIM rule]
                                                  [blank]
        provider    Anthropic                    [SECONDARY key] [TEXT value]
        model       claude-opus-4-5              [SECONDARY key] [TEXT value]
        config      ~/.opensre/opensre.json      [SECONDARY key] [BRAND path]
        env         .env                         [SECONDARY key] [BRAND path]
        credentials ~/.opensre/credentials.json  [SECONDARY key] [TEXT value]
        store       ~/.opensre/store.json        [SECONDARY key] [BRAND path]
    """
    from integrations.store import resolve_store_path

    console.print()
    console.print(Rule(style=DIM))

    done = Text()
    done.append(f"  {GLYPH_SUCCESS}  ", style=f"bold {HIGHLIGHT}")
    done.append("Done.", style=f"bold {TEXT}")
    console.print(done)

    console.print(Rule(style=DIM))
    console.print()

    key_col = 14

    def _kv(key: str, value: str, value_style: str = TEXT) -> None:
        row = Text()
        row.append(f"    {key:<{key_col}}", style=SECONDARY)
        row.append(value, style=value_style)
        console.print(row)

    _kv("provider", provider_label)
    _kv("model", model)
    _kv("config", saved_path, BRAND)
    _kv("env", env_path, BRAND)
    _kv("credentials", credential_line)
    _kv("store", str(resolve_store_path()), BRAND)
    console.print()


def render_integration_result(
    service_label: str,
    result: IntegrationHealthResult,
    *,
    github_display_level: str | None = None,
) -> None:
    if result.github_mcp is not None:
        from integrations.github import (
            GitHubMcpDisplayDetailLevel,
            print_github_mcp_validation_report,
        )

        print_github_mcp_validation_report(
            result.github_mcp,
            console=console,
            detail_level=cast(
                GitHubMcpDisplayDetailLevel,
                github_display_level or "standard",
            ),
        )
        return
    ok = bool(result.ok)
    detail = str(result.detail)
    glyph = GLYPH_SUCCESS if ok else GLYPH_ERROR
    glyph_style = f"bold {HIGHLIGHT}" if ok else f"bold {ERROR}"
    prefix = "Connected" if ok else "Failed"

    status_line = Text()
    status_line.append(f"  {glyph}  ", style=glyph_style)
    status_line.append(f"{service_label}", style=f"bold {TEXT}")
    status_line.append("  ·  ", style=DIM)
    status_line.append(prefix, style=TEXT)
    console.print(status_line)

    for raw_line in detail.splitlines():
        line = raw_line.strip()
        if line:
            detail_text = Text()
            detail_text.append(f"     {line}", style=SECONDARY)
            console.print(detail_text)


def render_next_steps() -> None:
    """Print suggested commands after onboarding."""
    console.print(Rule(style=DIM))

    section = Text()
    section.append("  What's next", style=SECONDARY)
    console.print(section)

    console.print(Rule(style=DIM))
    console.print()

    next_steps: tuple[tuple[str, str], ...] = (
        ("opensre", "Start the interactive agent"),
        ("opensre doctor", "Check this machine is ready"),
        ("opensre integrations setup github", "Optional: add GitHub when repository work needs it"),
        ("opensre onboard", "Re-run LLM setup at any time"),
    )

    for cmd, description in next_steps:
        cmd_line = Text()
        cmd_line.append(f"  {cmd}", style=f"bold {BRAND}")
        console.print(cmd_line)
        desc_line = Text()
        desc_line.append(f"    {description}", style=SECONDARY)
        console.print(desc_line)

    console.print()
