"""Identity banner rendered at REPL launch."""

from __future__ import annotations

import os

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from app.config import LLMSettings
from app.version import get_version


def _detect_provider_model() -> tuple[str, str]:
    """Return a human-readable (provider, model) for the active LLM config."""
    try:
        settings = LLMSettings.from_env()
    except Exception:  # noqa: BLE001
        return ("unknown", "unknown")

    provider = settings.provider or os.getenv("LLM_PROVIDER", "anthropic")
    model = getattr(settings, f"{provider}_reasoning_model", "") or "default"
    return (provider, model)


def render_banner(console: Console | None = None) -> None:
    """Print the REPL identity banner.

    The panel expands to the full terminal width, leaving only Rich's
    default 1-char margin on each side. Content inside is padded and
    centered for a clean Claude-Code-style welcome.
    """
    console = console or Console(highlight=False)
    provider, model = _detect_provider_model()

    title = Text()
    title.append("◉  ", style="bold cyan")
    title.append("OpenSRE", style="bold")
    title.append("  ·  ", style="dim")
    title.append(f"v{get_version()}", style="dim")

    info = Text()
    info.append("model  ", style="dim")
    info.append(f"{provider} · {model}", style="green")
    info.append("\n")
    info.append("mode   ", style="dim")
    info.append("interactive · read-only tools", style="")

    hints = Text()
    hints.append("/help", style="bold")
    hints.append(" for commands", style="dim")
    hints.append("   ·   ", style="dim")
    hints.append("/status", style="bold")
    hints.append(" for setup", style="dim")
    hints.append("   ·   ", style="dim")
    hints.append("/exit", style="bold")
    hints.append(" to quit", style="dim")

    body = Group(
        Align.center(title),
        Text(""),
        Align.center(info),
        Text(""),
        Align.center(hints),
    )

    console.print()
    console.print(
        Panel(
            body,
            border_style="cyan",
            padding=(1, 2),
            expand=True,
        )
    )
    console.print()
