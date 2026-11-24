"""Strip terminal control characters from model-supplied text.

Plan steps, menu titles, option labels, and assistant prose come from the
model and are later written into raw ANSI or Rich output. An embedded escape
(ESC, OSC, CR) could spoof the terminal or corrupt menu-row accounting, so
this removes control characters at the render/parse boundary.
"""

from __future__ import annotations

import re

# ``ESC [ <numbers> m``: select graphic rendition, the only sequence kept.
_STYLE_SEQUENCE = re.compile(r"\x1b\[[0-9;:]*m")

# LF / Tab keep multi-line markdown and report structure intact. ESC, CR, BEL,
# and the rest of C0/C1 still go — those are the spoof/row-corruption vectors.
_KEEP_WHITESPACE = frozenset("\n\t")


def strip_terminal_controls(text: str, *, keep_whitespace: bool = False) -> str:
    """Return ``text`` without C0/C1 control characters or DEL.

    Removes ``0x00``–``0x1F``, ``0x7F``, and ``0x80``–``0x9F``. When
    ``keep_whitespace`` is true, LF and Tab are kept so multi-line markdown
    bodies retain structure; ESC, CR, BEL, and other controls are still
    removed. All printable content, Unicode included, is preserved.
    """

    def _drop(ch: str) -> bool:
        code = ord(ch)
        if keep_whitespace and ch in _KEEP_WHITESPACE:
            return False
        return code < 0x20 or 0x7F <= code <= 0x9F

    return "".join(ch for ch in text if not _drop(ch))


def strip_non_style_escapes(text: str) -> str:
    """Return ``text`` with colour and style sequences kept and every other control gone.

    For output a command printed: its colours are worth keeping, but the bytes
    are whatever a log file, commit message or HTTP body contained. Anything
    other than a style sequence could write the clipboard, retitle the window,
    or move the cursor over an earlier line such as an approval prompt. Style
    sequences are kept whole and everything between them is filtered, so no
    removal can splice two fragments into a new sequence.
    """
    kept: list[str] = []
    position = 0
    for match in _STYLE_SEQUENCE.finditer(text):
        kept.append(strip_terminal_controls(text[position : match.start()], keep_whitespace=True))
        kept.append(match.group())
        position = match.end()
    kept.append(strip_terminal_controls(text[position:], keep_whitespace=True))
    return "".join(kept)


__all__ = ["strip_non_style_escapes", "strip_terminal_controls"]
