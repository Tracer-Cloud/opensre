"""Tool output must never be parsed as terminal markup.

A skill body, shell stdout, or model reply can contain square brackets — the
delivering-morning-briefings recipe embeds ``sed -E 's/<title><!\\[CDATA\\[//; s/\\]\\]>//'``.
Rendered as markup that reads as an unbalanced tag and raises ``MarkupError``,
which killed the whole turn with "turn error: closing tag ... doesn't match any
open tag" and no briefing.

The guard belongs at the surface: ``core/agent_harness/AGENTS.md`` forbids the
harness from depending on Rich, so the harness passes literal text and the sink
decides how to render it.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from core.agent_harness.prompts.skills import load_skill_body
from surfaces.interactive_shell.runtime.agent_harness_adapters import ShellOutputSink


def test_a_skill_body_is_not_valid_markup() -> None:
    """Pins why the sink must render literally, so the guard is not mistaken for noise."""
    # Arrange
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)

    # Act / Assert
    with pytest.raises(Exception, match="closing tag"):
        console.print(load_skill_body("delivering-morning-briefings"))


def test_the_shell_sink_renders_bracket_heavy_output_literally() -> None:
    """The turn must survive output containing bracket-heavy shell commands."""
    # Arrange
    buffer = io.StringIO()
    sink = ShellOutputSink(Console(file=buffer, force_terminal=False, highlight=False))
    body = load_skill_body("delivering-morning-briefings")

    # Act
    sink.print(body)

    # Assert
    assert "CDATA" in buffer.getvalue()


def test_the_harness_does_not_import_rich() -> None:
    """agent_harness/AGENTS.md: no terminal-UI dependency in the turn driver."""
    # Arrange
    from pathlib import Path

    source = Path("core/agent_harness/turns/action_driver.py").read_text(encoding="utf-8")

    # Assert
    assert "from rich" not in source
    assert "import rich" not in source
