"""Connection browsing is bounded, literal, and explicit about mutations."""

from __future__ import annotations

from os import terminal_size
from types import SimpleNamespace

import pytest
from prompt_toolkit.output.base import DummyOutput
from rich.text import Text

from surfaces.interactive_shell.ui import integration_browser as browser
from surfaces.shared.terminal.prompt_layout import prompt_text_width


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    events: list[str] = []
    state = SimpleNamespace(keys=iter(["cancel"]), width=80, height=24)

    class Output(DummyOutput):
        def enter_alternate_screen(self) -> None:
            events.append("screen")

        def quit_alternate_screen(self) -> None:
            events.append("restore")

    def size(**_kwargs: object) -> terminal_size:
        return terminal_size((state.width, state.height))

    def read(*, allow_chars: bool = False) -> str:
        assert allow_chars, "Action shortcuts require printable-character input"
        key = next(state.keys)
        if isinstance(key, Exception):
            raise key
        return str(key)

    monkeypatch.setattr(browser, "get_terminal_size", size)
    monkeypatch.setattr(browser, "create_output", Output)
    monkeypatch.setattr(browser, "read_menu_or_char", read)
    monkeypatch.setattr(browser, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(browser, "enter_inline_menu", lambda: events.append("enter"))
    monkeypatch.setattr(browser, "leave_inline_menu", lambda: events.append("leave"))
    state.events = events
    return state


@pytest.mark.parametrize("mcp", [False, True])
@pytest.mark.parametrize("size", [(8, 3), (39, 12), (59, 20), (99, 32), (119, 50)])
def test_frame_fits_and_keeps_selected_item_visible(mcp: bool, size: tuple[int, int]) -> None:
    width, height = size
    names = [browser.IntegrationEntry(f"connection-{n}", True) for n in range(40)]
    names[30] = browser.IntegrationEntry("界" * 45 + "\x1b[31m\r\nspoof", True)
    frame = browser._render_frame(names, selected=30, top=0, mcp=mcp, width=width, height=height)
    rows = [Text.from_ansi(row).plain for row in frame.rows]
    assert len(rows) < height
    assert all(prompt_text_width(row) <= width and "\n" not in row for row in rows)
    assert "Esc" in "\n".join(rows)
    if width >= 39 and height >= 12:
        assert any("›" in row and "Not checked" in row for row in rows)
        assert ("r disconnect" if mcp else "r remove") in "\n".join(rows)
        assert "31/40" in "\n".join(rows)


@pytest.mark.parametrize(
    "key,action", [("enter", "verify"), ("s", "setup"), ("r", "remove"), ("cancel", None)]
)
def test_actions_target_selection_only_after_explicit_key(
    terminal: SimpleNamespace, key: str, action: str | None
) -> None:
    terminal.keys = iter(["down", key])
    selected = browser.browse_integrations(
        [browser.IntegrationEntry("github", True), browser.IntegrationEntry("grafana", True)]
    )
    if action is None:
        assert selected is None
    else:
        assert selected is not None
        assert (selected.action, selected.service) == (action, "grafana")
    assert terminal.events == ["enter", "screen", "restore", "leave"]


def test_empty_browser_offers_setup_but_never_removes(terminal: SimpleNamespace) -> None:
    terminal.keys = iter(["enter", "r", "s", "down", "a"])
    assert browser.browse_integrations([], mcp=True) == browser.IntegrationSelection("setup")
    frame = browser._render_frame([], selected=0, top=0, mcp=True, width=79, height=24)
    text = "\n".join(Text.from_ansi(row).plain for row in frame.rows)
    assert "No MCP servers configured" in text and "a connect" in text
    assert "r disconnect" not in text


def test_read_failure_restores_terminal(terminal: SimpleNamespace) -> None:
    terminal.keys = iter([RuntimeError("read failed")])
    with pytest.raises(RuntimeError, match="read failed"):
        browser.browse_integrations([browser.IntegrationEntry("github", True)])
    assert terminal.events[-2:] == ["restore", "leave"]


def test_tiny_terminal_cannot_activate_hidden_actions(terminal: SimpleNamespace) -> None:
    terminal.width = 20
    terminal.keys = iter(["r", "enter", "a", "cancel"])
    assert browser.browse_integrations([browser.IntegrationEntry("github", True)]) is None


def test_non_tty_never_reads_input(
    terminal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(browser, "repl_tty_interactive", lambda: False)
    assert browser.browse_integrations([browser.IntegrationEntry("github", True)]) is None
    assert terminal.events == []


def test_unsupported_verification_is_not_offered_or_executed(terminal: SimpleNamespace) -> None:
    entries = [browser.IntegrationEntry("airflow", False)]
    frame = browser._render_frame(entries, selected=0, top=0, mcp=False, width=79, height=24)
    text = "\n".join(Text.from_ansi(row).plain for row in frame.rows)
    assert "No connectivity check available" in text
    assert "Configured" in text and "Enter verify" not in text
    terminal.keys = iter(["enter", "s"])
    assert browser.browse_integrations(entries) == browser.IntegrationSelection("setup", "airflow")
