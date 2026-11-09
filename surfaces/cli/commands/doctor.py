"""Full environment diagnostic command, inspired by ``fly doctor``.

Rendered output (colour roles):
──────────────────────────────────────────── [DIM rule]
  OpenSRE Doctor                             [TEXT bold section header]
──────────────────────────────────────────── [DIM rule]

  ✓  python          Python 3.13.2           [HIGHLIGHT ✓] [SECONDARY check] [TEXT detail]
  ✓  env_file        .env (12 keys)
  ⚠  integrations    no integrations         [WARNING ⚠]
  ✗  llm_provider    ANTHROPIC_API_KEY unset [ERROR ✗]
  ✓  version         <version> (up to date)
  ✓  network         github.com reachable

──────────────────────────────────────────── [DIM rule]
  1 error · 1 warning — run opensre doctor   [ERROR/WARNING counts · SECONDARY hint]
  All checks passed.                         [HIGHLIGHT — when clean]
"""

from __future__ import annotations

import json

import click
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from infrastructure.process.exit_codes import ERROR as EXIT_ERROR
from infrastructure.process.exit_codes import SUCCESS
from infrastructure.process.runtime_flags import is_json_output
from infrastructure.terminal.theme import (
    DIM,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    GLYPH_WARNING,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)
from surfaces.shared.doctor_checks import run_doctor_checks


def _render_doctor_results(console: Console, results: list[dict[str, str]]) -> None:
    """Print formatted diagnostic results with design-system colour roles.

    Section header: TEXT bold + DIM rule (col 45).
    ✓ checks:  HIGHLIGHT glyph, SECONDARY check name, TEXT detail.
    ⚠ warnings: WARNING glyph, SECONDARY check name, TEXT detail.
    ✗ errors:  ERROR glyph, SECONDARY check name, TEXT detail.
    Summary:   ERROR count + WARNING count in their respective roles, SECONDARY hint.
    """
    console.print()
    console.print(Rule(style=DIM))

    # Section header — TEXT weight, DIM rule to col 45.
    header = Text()
    header.append("  OpenSRE Doctor", style=f"bold {TEXT}")
    console.print(header)

    console.print(Rule(style=DIM))
    console.print()

    check_col = 18  # fixed column width for check name alignment

    for r in results:
        status = r["status"]

        if status == "ok":
            glyph = GLYPH_SUCCESS
            glyph_style = f"bold {HIGHLIGHT}"
            detail_style = TEXT
        elif status == "warn":
            glyph = GLYPH_WARNING
            glyph_style = f"bold {WARNING}"
            detail_style = TEXT
        else:
            glyph = GLYPH_ERROR
            glyph_style = f"bold {ERROR}"
            detail_style = TEXT

        row = Text()
        row.append(f"  {glyph}  ", style=glyph_style)
        row.append(f"{r['check']:<{check_col}}", style=SECONDARY)
        row.append(r["detail"], style=detail_style)
        console.print(row)

    console.print()
    console.print(Rule(style=DIM))

    error_count = sum(1 for r in results if r["status"] == "error")
    warn_count = sum(1 for r in results if r["status"] == "warn")

    if error_count == 0 and warn_count == 0:
        summary = Text()
        summary.append(f"  {GLYPH_SUCCESS}  ", style=f"bold {HIGHLIGHT}")
        summary.append("All checks passed.", style=TEXT)
        console.print(summary)
    else:
        summary = Text()
        summary.append("  ")
        if error_count:
            summary.append(
                f"{error_count} error{'s' if error_count > 1 else ''}", style=f"bold {ERROR}"
            )
        if error_count and warn_count:
            summary.append("  ·  ", style=SECONDARY)
        if warn_count:
            summary.append(
                f"{warn_count} warning{'s' if warn_count > 1 else ''}", style=f"bold {WARNING}"
            )
        summary.append("   —   fix and rerun ", style=SECONDARY)
        summary.append("opensre doctor", style=f"bold {TEXT}")
        console.print(summary)

    console.print()


@click.command(name="doctor")
def doctor_command() -> None:
    """Run a full environment diagnostic to surface setup issues."""
    results = run_doctor_checks()

    if is_json_output():
        click.echo(json.dumps(results, indent=2))
    else:
        console = Console(
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
        )
        _render_doctor_results(console, results)

    has_errors = any(r["status"] == "error" for r in results)
    raise SystemExit(EXIT_ERROR if has_errors else SUCCESS)
