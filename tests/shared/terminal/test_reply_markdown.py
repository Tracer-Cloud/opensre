"""Reply markdown tables wrap long cells and mark the header, not the body."""

from __future__ import annotations

import io

from rich.console import Console
from rich.text import Text

import infrastructure.terminal.theme as ui_theme
from infrastructure.terminal.markdown import ReplyMarkdown

_TABLE = "| File | Jobs |\n| --- | --- |\n| .github/workflows/celebrate-merged-pr.yml | 1 |\n"


def _render(width: int) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=width)
    with console.use_theme(ui_theme.MARKDOWN_THEME):
        console.print(ReplyMarkdown(_TABLE))
    return buf.getvalue()


def test_long_cell_folds_instead_of_truncating() -> None:
    # Arrange: the file name cannot fit the column at this width.
    width = 40

    # Act
    plain = Text.from_ansi(_render(width)).plain

    # Assert: the name continues on the next line and no ellipsis was inserted.
    assert "…" not in plain
    first_column = "".join(line.split("│")[0].strip() for line in plain.splitlines())
    assert ".github/workflows/celebrate-merged-pr.yml" in first_column
    assert "│" in plain


def test_header_is_bold_and_body_is_not() -> None:
    # Arrange / Act
    text = Text.from_ansi(_render(80))

    # Assert
    header_at = text.plain.index("File")
    body_at = text.plain.index("celebrate")
    header_bold = any(
        span.start <= header_at < span.end and span.style.bold  # type: ignore[union-attr]
        for span in text.spans
        if not isinstance(span.style, str)
    )
    body_bold = any(
        span.start <= body_at < span.end and span.style.bold  # type: ignore[union-attr]
        for span in text.spans
        if not isinstance(span.style, str)
    )
    assert header_bold
    assert not body_bold
