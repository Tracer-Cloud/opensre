"""Tests for Rich rendering helpers used by the interactive shell."""

from __future__ import annotations

import io
import re
import threading

import pytest
from rich.console import Console

from surfaces.interactive_shell.runtime.core.state import SpinnerState
from surfaces.interactive_shell.ui.poster import refresh_welcome_poster, repl_render_launch_poster
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole
from surfaces.shared.terminal.components.rendering import (
    _repl_write_buffer,
    print_repl_json,
    print_repl_renderable,
    print_repl_text,
    repl_print,
    repl_table,
)
from surfaces.shared.terminal.tables import (
    render_integrations_table,
    render_mcp_table,
)


def test_repl_table_minimal_box() -> None:
    t = repl_table(title="T")
    assert t.title == "T"


def test_print_repl_json_tty_uses_single_buffered_write(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake = _FakeStdout()
    monkeypatch.setattr("surfaces.shared.terminal.components.rendering.sys.stdout", fake)
    console = Console(file=fake, force_terminal=True, highlight=False)
    print_repl_json(console, '{"ok": true}')
    joined = "".join(fake.writes)
    assert "\r\n" in joined
    assert joined.count("\n") == joined.count("\r\n")


def test_print_repl_text_uses_crlf_so_goal_checklists_do_not_staircase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under patch_stdout(raw=True), bare \\n staircases; progress must use CRLF."""

    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake = _FakeStdout()
    monkeypatch.setattr("surfaces.shared.terminal.components.rendering.sys.stdout", fake)
    # Console.file must be the same object as sys.stdout for the TTY path.
    console = Console(file=fake, force_terminal=True, highlight=False)
    monkeypatch.setattr(console, "file", fake)
    text = (
        "◎ /goal active · working… · 14s · turn 1/6 · +0 tokens\n"
        "  condition: Fully complete all three steps\n"
        "  reason: working - starting session-goal turn 2/6\n"
        "  Checklist:\n"
        "    [ ] 1. Identify the checkout latency metric\n"
        "  → [ ] 2. Run the three-step latency check\n"
        "    [ ] 3. Verify and report all results"
    )
    print_repl_text(console, text, markup=False)
    joined = "".join(fake.writes)
    assert joined.startswith("\r")
    assert "\r\n  Checklist:" in joined
    assert "\r\n    [ ] 1. Identify" in joined
    assert "\r\n  → [ ] 2. Run" in joined
    # No bare LF left (would staircase under raw patch_stdout).
    assert "\n" not in joined.replace("\r\n", "")


def test_print_repl_renderable_keeps_truecolor_and_crlf(monkeypatch: pytest.MonkeyPatch) -> None:
    from rich.console import Group
    from rich.text import Text

    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake = _FakeStdout()
    monkeypatch.setattr("surfaces.shared.terminal.components.rendering.sys.stdout", fake)
    console = Console(file=fake, force_terminal=True, highlight=False, color_system="truecolor")
    monkeypatch.setattr(console, "file", fake)

    print_repl_renderable(console, Group(Text("Plan complete"), Text("  ✓ step", style="#6E6E6E")))

    joined = "".join(fake.writes)
    assert "38;2;110;110;110" in joined
    assert "\n" not in joined.replace("\r\n", "")


def test_print_repl_json_records_column_zero_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("surfaces.shared.terminal.components.rendering.sys.stdout", fake_stdout)

    console = Console(file=fake_stdout, force_terminal=True, width=80)
    print_repl_json(console, '{"ok": true}')

    assert len(fake_stdout.writes) == 1
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", fake_stdout.writes[0])
    # Column-zero reset, then the leading blank line: immune to wherever a
    # spinner teardown or wrapped log line left the cursor.
    assert rendered.startswith("\r\r\n")
    assert '"ok": true' in rendered


def test_print_repl_table_stays_on_the_crlf_path_while_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every slash command records its console; that must not force row-by-row prints.

    Under ``patch_stdout(raw=True)`` each ``console.print`` row ends in a bare
    ``\\n`` and the table staircases. Recording is fed from the buffered write
    instead, so the screen gets one CRLF write and the transcript still sees the
    table.
    """
    from rich.table import Table

    from surfaces.shared.terminal.components.rendering import print_repl_table

    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    # Arrange — a recording console (what capture_console_segment does) on a TTY
    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)
    console = Console(force_terminal=True, width=80)
    console.record = True
    table = Table()
    table.add_column("service")
    table.add_column("status")
    table.add_row("github", "passed")
    table.add_row("slack", "passed")

    # Act
    print_repl_table(console, table, width=60)

    # Assert — one CRLF write on screen, and the recorder still has the rows
    assert len(fake_stdout.writes) == 1
    written = fake_stdout.writes[0]
    assert written.count("\n") == written.count("\r\n")
    assert "github" in written
    exported = console.export_text(clear=True)
    assert "github" in exported and "slack" in exported


def test_render_integrations_table_empty_shows_hint() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    render_integrations_table(console, [])
    assert "opensre integrations setup" in buf.getvalue()


def test_repl_print_resets_before_each_line(monkeypatch) -> None:
    resets: list[bool] = []

    monkeypatch.setattr(
        "surfaces.shared.terminal.components.choice_menu.prepare_repl_output_line",
        lambda: resets.append(True),
    )

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    repl_print(console, "line one")
    repl_print(console, "line two")

    assert len(resets) == 2


def test_repl_print_does_not_double_prepare_with_streaming_console(monkeypatch) -> None:
    resets: list[bool] = []

    monkeypatch.setattr(
        "surfaces.shared.terminal.components.choice_menu.prepare_repl_output_line",
        lambda: resets.append(True),
    )

    console = StreamingConsole(
        SpinnerState(),
        threading.Event(),
        file=io.StringIO(),
        force_terminal=False,
        width=80,
    )
    repl_print(console, "line")

    assert len(resets) == 1


def test_repl_print_streaming_console_prepares_tty_once_when_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.choice_menu.repl_tty_interactive",
        lambda: True,
    )

    console = StreamingConsole(
        SpinnerState(),
        threading.Event(),
        file=io.StringIO(),
        force_terminal=False,
        width=80,
    )
    repl_print(console, "line")

    assert fake_stdout.writes == ["\r\n", "\r"]


def test_repl_render_launch_poster_uses_crlf_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return True

    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)

    from infrastructure.terminal.theme import THEME_REGISTRY, set_active_theme

    def _rgb(hex_color: str) -> str:
        h = hex_color.lstrip("#")
        return f"{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}"

    set_active_theme("blue")
    console = Console(
        file=fake_stdout,
        force_terminal=True,
        highlight=False,
        color_system="truecolor",
        width=120,
    )
    repl_render_launch_poster(console, theme_notice="blue")

    written = "".join(fake_stdout.writes)
    assert "theme set:" in written
    assert "blue" in written
    assert f"38;2;{_rgb(THEME_REGISTRY['blue'].HIGHLIGHT)}" in written
    assert _rgb(THEME_REGISTRY["green"].HIGHLIGHT) not in written
    # Canonical braille "loops" mark — do not assert a box-drawing wordmark.
    assert "⣿⣿⠀⠀⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⠀⢸⣿⣿" in written
    assert "Skills" in written
    assert "\r\n" in written
    # REPL path must not emit bare \\n (causes double-spaced output under patch_stdout).
    assert "\r" not in written.replace("\r\n", "")


def test_repl_write_buffer_strips_only_escaped_cpr_sequences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeStdout:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    fake_stdout = _FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)

    _repl_write_buffer("\x1b[1;1Rtheme set: pink 12;5R\r\n")

    written = "".join(fake_stdout.writes)
    assert "theme set: pink" in written
    assert "12;5R" in written
    assert "\x1b[1;1R" not in written


def test_refresh_welcome_poster_drains_cpr_after_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    drains: list[str] = []

    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.poster.repl_clear_screen",
        lambda: drains.append("clear"),
    )
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.cpr_stdin.drain_stale_cpr_bytes",
        lambda: drains.append("drain"),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.poster.repl_render_launch_poster",
        lambda *_args, **_kwargs: drains.append("render"),
    )

    console = Console(file=io.StringIO(), force_terminal=False)
    refresh_welcome_poster(console)

    assert drains == ["clear", "drain", "render"]


def test_render_integrations_table_renders_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """print_repl_table on a non-TTY console writes via console.print to stdout.

    The cursor-reset (prepare_repl_output_line) is no longer called from the
    table rendering path; on a real TTY the blank line and \\r\\n normalisation
    are folded into a single sys.stdout.write call in print_repl_table.
    """
    console = Console(force_terminal=False, width=80)
    render_integrations_table(
        console,
        [
            {
                "service": "grafana",
                "source": "local store",
                "status": "passed",
                "detail": "Connected to https://example.grafana.net",
            }
        ],
    )

    assert "grafana" in capsys.readouterr().out


def test_render_integrations_table_sorts_services_and_includes_mcp(
    capsys: pytest.CaptureFixture[str],
) -> None:
    console = Console(force_terminal=False, width=80)
    render_integrations_table(
        console,
        [
            {"service": "sentry", "source": "-", "status": "missing", "detail": "missing"},
            {"service": "github", "source": "-", "status": "missing", "detail": "missing"},
            {"service": "datadog", "source": "env", "status": "passed", "detail": "ok"},
        ],
    )

    output = capsys.readouterr().out
    assert output.index("datadog") < output.index("github") < output.index("sentry")
    assert "github" in output


def test_render_mcp_table_renders_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    console = Console(force_terminal=False, width=80)
    render_mcp_table(
        console,
        [
            {
                "service": "github",
                "source": "local store",
                "status": "configured",
                "detail": "Connected",
            }
        ],
    )

    assert "github" in capsys.readouterr().out
