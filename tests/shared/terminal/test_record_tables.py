"""Record tables reflow on replay instead of freezing their original column widths."""

import io

from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def test_same_record_reflows_after_resize_without_losing_literal_content() -> None:
    table = RecordTable(
        "Tasks",
        (RecordColumn("Task"), RecordColumn("State", 10)),
        (
            RecordRow(
                (Text("[bold]Checkout 日本 checks[/bold]"), Text("Running")),
                (Text("ID: abc123def45678901234567890123456"),),
            ),
        ),
    )
    for width in (120, 40, 80):
        output = io.StringIO()
        Console(file=output, width=width, height=25, color_system=None).print(table)
        text = output.getvalue()
        assert "abc123def45678901234567890123456" in text
        assert "[bold]" in text
        assert "Running" in text
        assert ("State:" in text) == (width < 72)
        assert max(cell_len(line) for line in text.splitlines()) <= width
        assert "\x1b" not in text
