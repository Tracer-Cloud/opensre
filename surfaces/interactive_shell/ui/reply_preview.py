"""Live preview of a model reply while it streams.

The REPL cannot redraw scrollback in place (``patch_stdout`` owns it), so the
newest rows of the in-progress reply show in the pinned prompt region, above
the status row. The preview is transient: the turn still prints the finished
reply once, through its usual rendering, and the preview is cleared.
"""

from __future__ import annotations

import textwrap

from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal import theme as ui_theme
from surfaces.shared.terminal.prompt_layout import clip_prompt_text

#: Newest rows shown; few enough that the prompt region stays compact.
REPLY_PREVIEW_ROWS = 4
_INDENT = "  "


def reply_preview_ansi(text: str, *, width: int) -> str:
    """The last rows of ``text`` wrapped to ``width`` columns; empty when blank.

    Each row is clipped to the width so none can soft-wrap and desync the
    prompt region's row count. Model text is stripped of terminal controls.
    """
    visible = strip_terminal_controls(text, keep_whitespace=True).expandtabs(4)
    body_width = max(width - len(_INDENT), 1)
    rows: list[str] = []
    # Only the trailing lines can reach the last rows.
    for line in visible.splitlines()[-REPLY_PREVIEW_ROWS:]:
        rows.extend(textwrap.wrap(line, body_width))
    return "\n".join(
        f"{_INDENT}{ui_theme.SECONDARY_ANSI}{clip_prompt_text(row, body_width)}{ui_theme.ANSI_RESET}"
        for row in rows[-REPLY_PREVIEW_ROWS:]
    )


__all__ = ["REPLY_PREVIEW_ROWS", "reply_preview_ansi"]
