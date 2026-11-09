"""Tests for /history subcommands and /privacy."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from prompt_toolkit.history import FileHistory, InMemoryHistory
from rich.console import Console

from surfaces.interactive_shell import prompt_history as history_module
from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.prompt_history.policy import RedactingFileHistory
from surfaces.interactive_shell.session import Session


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def _redirect_history_path(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """Point ``prompt_history_path()`` at a tmp file across all importers."""
    from surfaces.interactive_shell.command_registry import privacy_cmds as privacy_cmds_module
    from surfaces.interactive_shell.prompt_history import storage as history_storage

    monkeypatch.setattr(history_module, "prompt_history_path", lambda: target)
    monkeypatch.setattr(privacy_cmds_module, "prompt_history_path", lambda: target)
    monkeypatch.setattr(history_storage, "prompt_history_path", lambda: target)


class TestHistoryClear:
    def test_truncates_existing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        history_file = tmp_path / "history"
        history_file.write_text("# 2026-01-01 00:00:00\n+older\n", encoding="utf-8")
        _redirect_history_path(monkeypatch, history_file)

        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/history clear", session, console) is True

        assert history_file.read_text(encoding="utf-8") == ""
        assert "cleared" in buf.getvalue()

    def test_handles_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        history_file = tmp_path / "history"  # never created
        _redirect_history_path(monkeypatch, history_file)

        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/history clear", session, console) is True
        assert "cleared" in buf.getvalue()


class TestHistoryToggle:
    def test_off_then_on_flips_paused_flag(self, tmp_path: Path) -> None:
        backend = RedactingFileHistory(str(tmp_path / "history"))
        session = Session()
        session.terminal.prompt_history_backend = backend

        console, _ = _capture()
        dispatch_slash("/history off", session, console)
        assert backend.paused is True

        dispatch_slash("/history on", session, console)
        assert backend.paused is False

    def test_off_with_in_memory_backend_is_noop_message(self) -> None:
        session = Session()
        session.terminal.prompt_history_backend = InMemoryHistory()
        console, buf = _capture()

        dispatch_slash("/history off", session, console)
        assert "in-memory" in buf.getvalue() or "not persisting" in buf.getvalue()

    def test_file_history_backend_reports_runtime_pause_is_unavailable(
        self, tmp_path: Path
    ) -> None:
        session = Session()
        session.terminal.prompt_history_backend = FileHistory(str(tmp_path / "history"))
        console, buf = _capture()

        dispatch_slash("/history off", session, console)
        assert "without redaction" in buf.getvalue()

    def test_file_history_backend_reports_persistence_already_on(self, tmp_path: Path) -> None:
        session = Session()
        session.terminal.prompt_history_backend = FileHistory(str(tmp_path / "history"))
        console, buf = _capture()

        dispatch_slash("/history on", session, console)
        assert "already on" in buf.getvalue()


class TestHistoryRetention:
    def test_sets_cap_and_prunes(self, tmp_path: Path) -> None:
        history_file = tmp_path / "history"
        backend = RedactingFileHistory(str(history_file), max_entries=10)
        for i in range(5):
            backend.store_string(f"entry-{i}")

        session = Session()
        session.terminal.prompt_history_backend = backend
        console, _ = _capture()
        dispatch_slash("/history retention 2", session, console)

        persisted = list(reversed(list(backend.load_history_strings())))
        assert persisted == ["entry-3", "entry-4"]
        assert backend._max_entries == 2

    def test_without_a_cap_asks_for_one_when_the_turn_owns_stdin(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``/history retention`` must collect a cap, not close on usage text.

        The same handler serves the typed command, the /history menu, and a
        /help selection, so all three land on one picker.
        """
        from surfaces.interactive_shell.command_registry import privacy_cmds

        backend = RedactingFileHistory(str(tmp_path / "history"))
        session = Session()
        session.terminal.prompt_history_backend = backend
        session.terminal.exclusive_stdin_active = True
        monkeypatch.setattr(privacy_cmds, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(privacy_cmds, "_prompt_retention_cap", lambda: "500")

        console, buf = _capture()
        dispatch_slash("/history retention", session, console)

        assert backend._max_entries == 500
        assert "usage:" not in buf.getvalue()

    def test_dismissing_the_cap_picker_leaves_the_setting_alone(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from surfaces.interactive_shell.command_registry import privacy_cmds

        backend = RedactingFileHistory(str(tmp_path / "history"), max_entries=10)
        session = Session()
        session.terminal.prompt_history_backend = backend
        session.terminal.exclusive_stdin_active = True
        monkeypatch.setattr(privacy_cmds, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(privacy_cmds, "_prompt_retention_cap", lambda: None)

        console, buf = _capture()
        assert dispatch_slash("/history retention", session, console) is True

        assert backend._max_entries == 10
        assert "usage:" not in buf.getvalue()

    def test_without_exclusive_stdin_it_still_prints_usage(self, tmp_path: Path) -> None:
        backend = RedactingFileHistory(str(tmp_path / "history"))
        session = Session()
        session.terminal.prompt_history_backend = backend
        assert session.terminal.exclusive_stdin_active is False

        console, buf = _capture()
        dispatch_slash("/history retention", session, console)

        assert "usage:" in buf.getvalue()

    def test_rejects_non_integer(self, tmp_path: Path) -> None:
        backend = RedactingFileHistory(str(tmp_path / "history"))
        session = Session()
        session.terminal.prompt_history_backend = backend
        console, buf = _capture()

        dispatch_slash("/history retention oops", session, console)
        assert "non-negative integer" in buf.getvalue()

    def test_rejects_negative(self, tmp_path: Path) -> None:
        backend = RedactingFileHistory(str(tmp_path / "history"))
        session = Session()
        session.terminal.prompt_history_backend = backend
        console, buf = _capture()

        dispatch_slash("/history retention -1", session, console)
        assert "non-negative integer" in buf.getvalue()

    def test_zero_sets_unlimited_without_crashing(self, tmp_path: Path) -> None:
        history_file = tmp_path / "history"
        backend = RedactingFileHistory(str(history_file), max_entries=2)
        for i in range(4):
            backend.store_string(f"entry-{i}")

        session = Session()
        session.terminal.prompt_history_backend = backend
        console, buf = _capture()

        assert dispatch_slash("/history retention 0", session, console) is True
        persisted = list(reversed(list(backend.load_history_strings())))
        assert persisted == ["entry-2", "entry-3"]
        assert backend.max_entries == 0
        assert "retention cap set to 0" in buf.getvalue()


class TestHistoryUnknownSubcommand:
    def test_prints_usage(self) -> None:
        session = Session()
        console, buf = _capture()
        dispatch_slash("/history bogus", session, console)
        assert "usage:" in buf.getvalue()


class TestPrivacyCommand:
    def test_shows_redacting_state(self, tmp_path: Path) -> None:
        backend = RedactingFileHistory(str(tmp_path / "history"))
        session = Session()
        session.terminal.prompt_history_backend = backend
        console, buf = _capture()

        dispatch_slash("/privacy", session, console)
        out = buf.getvalue()
        assert "Privacy settings" in out
        assert "persistence" in out
        assert "redaction" in out
        assert "threat model" in out

    def test_shows_paused_state_when_off(self, tmp_path: Path) -> None:
        backend = RedactingFileHistory(str(tmp_path / "history"))
        backend.paused = True
        session = Session()
        session.terminal.prompt_history_backend = backend
        console, buf = _capture()

        dispatch_slash("/privacy", session, console)
        assert "paused" in buf.getvalue()

    def test_in_memory_backend_reports_off(self) -> None:
        session = Session()
        session.terminal.prompt_history_backend = InMemoryHistory()
        console, buf = _capture()

        dispatch_slash("/privacy", session, console)
        assert "in-memory" in buf.getvalue()

    def test_file_history_backend_reports_no_redaction(self, tmp_path: Path) -> None:
        session = Session()
        session.terminal.prompt_history_backend = FileHistory(str(tmp_path / "history"))
        console, buf = _capture()

        dispatch_slash("/privacy", session, console)
        out = buf.getvalue()
        assert "on (no redaction)" in out
        assert "redaction" in out
