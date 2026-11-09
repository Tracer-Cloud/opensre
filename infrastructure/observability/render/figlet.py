"""Optional pyfiglet rendering for terminal banners."""

from __future__ import annotations


def render_figlet(text: str, *, font: str, max_line_width: int) -> str | None:
    """Return figlet art when pyfiglet is installed and output fits the terminal."""
    try:
        import pyfiglet
    except ImportError:
        return None

    try:
        rendered = str(pyfiglet.figlet_format(text, font=font)).rstrip()
    except Exception:
        return None

    if not rendered:
        return None
    if any(len(line) > max_line_width for line in rendered.splitlines()):
        return None
    return rendered
