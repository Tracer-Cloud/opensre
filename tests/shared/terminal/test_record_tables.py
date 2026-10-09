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


def test_short_records_keep_columns_grouped_on_extra_wide_terminals() -> None:
    table = RecordTable(
        "Work items",
        (RecordColumn("Work item"), RecordColumn("State", 10), RecordColumn("Due", 19)),
        (
            RecordRow((Text("test"), Text("Open"), Text("2026-10-10 00:00:00"))),
            RecordRow((Text("another test"), Text("Blocked"), Text("—"))),
        ),
    )
    positions = []
    for width in (80, 120, 160, 240):
        output = io.StringIO()
        Console(file=output, width=width, color_system=None).print(table)
        lines = output.getvalue().splitlines()
        header = next(line for line in lines if "State" in line)
        state_column = header.index("State")
        assert 20 <= state_column <= 30
        assert next(line for line in lines if "Open" in line).index("Open") == state_column
        assert next(line for line in lines if "Blocked" in line).index("Blocked") == state_column
        assert max(cell_len(line.rstrip()) for line in lines) < 80
        positions.append(state_column)
    assert len(set(positions)) == 1


def test_primary_column_measures_display_cells_and_longest_line() -> None:
    title = "日" * 15
    table = RecordTable(
        "Tasks",
        (RecordColumn("Task"), RecordColumn("State", 10)),
        (
            RecordRow((Text("short"), Text("Open"))),
            RecordRow((Text(title + "\nsmall"), Text("Running"))),
        ),
    )
    output = io.StringIO()
    Console(file=output, width=160, color_system=None).print(table)
    lines = output.getvalue().splitlines()
    header = next(line for line in lines if "State" in line)
    assert header.index("State") == 33  # 30 display cells plus the shared column gap.
    row = next(line for line in lines if "Running" in line)
    assert title in row
    assert cell_len(row.split("Running")[0]) == 33


def test_long_titles_wrap_with_a_readable_width_instead_of_stretching() -> None:
    title = "Check " + "production " * 12 + "checkout health"
    table = RecordTable(
        "Tasks",
        (RecordColumn("Task"), RecordColumn("State", 10)),
        (RecordRow((Text(title), Text("Running"))),),
    )
    output = io.StringIO()
    Console(file=output, width=240, color_system=None).print(table)
    text = output.getvalue()
    header = next(line for line in text.splitlines() if "State" in line)
    assert header.index("State") <= 55
    assert "checkout health" in text and "…" not in text
    assert text.count("production") == 12
    assert max(cell_len(line.rstrip()) for line in text.splitlines()) <= 65
