"""The action log groups consecutive same-kind calls into collapsible sections."""

from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.session.terminal_session import ActionLogEntry
from surfaces.interactive_shell.ui.action_log import flush_action_log


def _tty(buffer: io.StringIO) -> Console:
    return Console(file=buffer, force_terminal=True, highlight=False, color_system="truecolor")


def _push(session: Session, call_id: str, kind: str, concise: str, detail: str) -> None:
    session.terminal.push_action_log(
        ActionLogEntry(call_id=call_id, kind=kind, concise=concise, detail=detail)
    )


def test_consecutive_same_kind_calls_group_into_one_bordered_section() -> None:
    session = Session()
    _push(session, "1", "GitHub CLI", "gh repo view", "⏺ GitHub CLI · gh repo view")
    _push(session, "2", "GitHub CLI", "gh pr list", "⏺ GitHub CLI · gh pr list")
    buffer = io.StringIO()

    flush_action_log(_tty(buffer), session)

    out = buffer.getvalue()
    assert "GitHub CLI · 2 actions" in out  # one section header with the count
    assert all(corner in out for corner in ("╭", "╮", "╰", "╯"))  # full box border
    assert "gh repo view" in out  # concise rows, no dotted args
    assert "gh pr list" in out
    assert "Ctrl+O to expand details" in out
    assert session.terminal.has_action_log() is False  # buffer drained
    # Full detail is reachable via Ctrl+O.
    assert "gh repo view" in session.terminal.next_collapsed_output_for_expand()


def test_no_inline_dotted_arguments_on_a_single_call() -> None:
    session = Session()
    # A generic tool: concise is empty (label only), args live in the detail.
    _push(
        session,
        "1",
        "list github actions workflow runs",
        "",
        "⏺ list github actions workflow runs\n    owner: Tracer-Cloud\n    per_page: 100",
    )
    buffer = io.StringIO()

    flush_action_log(_tty(buffer), session)

    out = buffer.getvalue()
    assert "list github actions workflow runs" in out
    assert " · " not in out  # no dotted argument strip on the visible line
    assert "per_page" not in out  # args are hidden behind Ctrl+O


def test_a_lone_call_is_a_dim_line_not_a_one_row_box() -> None:
    session = Session()
    _push(session, "1", "GitHub CLI", "gh pr list", "⏺ GitHub CLI · gh pr list")
    buffer = io.StringIO()

    flush_action_log(_tty(buffer), session)

    out = buffer.getvalue()
    assert "gh pr list" in out
    assert not any(corner in out for corner in ("╭", "╮", "╰", "╯"))  # no box for one call


def test_two_different_lone_kinds_render_as_two_dim_lines_no_box() -> None:
    session = Session()
    _push(session, "1", "GitHub CLI", "gh pr list", "d1")
    _push(session, "2", "opensre", "opensre cron list", "d2")
    buffer = io.StringIO()

    flush_action_log(_tty(buffer), session)

    out = buffer.getvalue()
    assert out.count("╭") == 0  # neither group reaches 2 same-kind calls
    assert "gh pr list" in out
    assert "opensre cron list" in out


def test_non_tty_inlines_the_detail() -> None:
    session = Session()
    _push(session, "1", "GitHub CLI", "gh pr list", "⏺ GitHub CLI · gh pr list\n  ↳ 4 open PRs")
    buffer = io.StringIO()

    flush_action_log(Console(file=buffer, force_terminal=False, highlight=False), session)

    out = buffer.getvalue()
    assert "gh pr list" in out
    assert "4 open PRs" in out
    assert "Ctrl+O" not in out


def test_same_kind_calls_across_iterations_flush_once_as_one_group() -> None:
    # Greptile P1 regression: the observer must not flush per ReAct iteration,
    # or same-kind calls in separate iterations render as separate groups. The
    # sink flushes once, just before the reply, so they stay in one section.
    from surfaces.interactive_shell.runtime.agent_harness_adapters import ShellOutputSink
    from surfaces.interactive_shell.ui.action_rendering import ActionRenderObserver

    buffer = io.StringIO()
    console = _tty(buffer)
    session = Session()
    observer = ActionRenderObserver(session=session, console=console, message="check CI")

    # Iteration 1: one GitHub CLI call.
    observer("tool_start", {"id": "t1", "name": "github_cli", "input": {"args": ["pr", "list"]}})
    observer("tool_end", {"id": "t1", "name": "github_cli", "output": {"ok": True}})
    # Iteration 2: another GitHub CLI call.
    observer("tool_start", {"id": "t2", "name": "github_cli", "input": {"args": ["repo", "view"]}})
    observer("tool_end", {"id": "t2", "name": "github_cli", "output": {"ok": True}})

    # Nothing is flushed on the per-iteration drains.
    assert buffer.getvalue() == ""
    assert len(session.terminal.action_log_entries) == 2

    # The reply stream flushes them once, grouped.
    ShellOutputSink(console, session).stream(label="assistant", chunks=iter(["done"]))
    out = buffer.getvalue()
    assert "GitHub CLI · 2 actions" in out
    assert out.count("╭") == 1  # one box, not two


def test_a_tty_flush_is_one_buffered_write_of_every_row(monkeypatch) -> None:  # noqa: ANN001
    """Two lone calls flushed row by row staircased under the raw stdout patch."""
    # Arrange: two different lone tool calls and a spy on the CRLF-safe writer.
    from surfaces.interactive_shell.ui import action_log

    session = Session()
    _push(session, "1", "summarize github pr status", "", "d1")
    _push(session, "2", "propose scheduled delivery", "", "d2")
    writes: list[object] = []
    monkeypatch.setattr(
        action_log, "print_repl_renderable", lambda _console, renderable: writes.append(renderable)
    )

    # Act
    flush_action_log(_tty(io.StringIO()), session)

    # Assert: one write carrying both rows, so every row starts at column zero.
    assert len(writes) == 1
    rendered = [str(row) for row in writes[0].renderables]  # type: ignore[attr-defined]
    assert rendered[0] == ""
    assert rendered[1] == "⏺ summarize github pr status"
    assert rendered[2] == "⏺ propose scheduled delivery"
