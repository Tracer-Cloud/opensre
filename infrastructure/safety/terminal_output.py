"""Strip terminal control characters from model-supplied text.

Plan steps, menu titles, option labels, and assistant prose come from the
model and are later written into raw ANSI or Rich output. An embedded escape
(ESC, OSC, CR) could spoof the terminal or corrupt menu-row accounting, so
this removes control characters at the render/parse boundary.
"""

from __future__ import annotations

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


__all__ = ["strip_terminal_controls"]
