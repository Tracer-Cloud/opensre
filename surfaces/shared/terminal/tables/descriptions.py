"""Bounded, secondary description lines for responsive record lists."""

from rich.text import Text

from infrastructure.terminal.theme import SECONDARY


def description_details(
    description: str, *, width: int | None = 80, style: str | None = None
) -> tuple[Text, ...]:
    """Return a spaced, muted preview, or no lines for an empty description."""
    normalized = " ".join(description.split())
    if not normalized:
        return ()
    preview = Text(normalized, style=str(SECONDARY) if style is None else style)
    if width is not None:
        preview.truncate(width, overflow="ellipsis")
    return Text(""), preview
