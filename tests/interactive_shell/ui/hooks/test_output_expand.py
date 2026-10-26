"""Ctrl+O expands stashed peeks: ring cycle + inline-vs-pager."""

from __future__ import annotations

from surfaces.interactive_shell.session.terminal_session import (
    COLLAPSED_OUTPUT_RING_SIZE,
    COLLAPSED_STASH_MAX_CHARS,
    INLINE_EXPAND_MAX_CHARS,
    TerminalSession,
)
from surfaces.interactive_shell.ui.hooks import install_output_expand_key_bindings
from surfaces.interactive_shell.ui.hooks.output_expand import expand_collapsed_output


def _binding_keys(kb) -> set[tuple[str, ...]]:
    return {tuple(str(key) for key in binding.keys) for binding in kb.bindings}


def test_expand_binding_is_ctrl_o() -> None:
    kb = install_output_expand_key_bindings(lambda: True, lambda: "body", lambda _text: None)
    assert ("Keys.ControlO",) in _binding_keys(kb)


def test_ctrl_o_expands_the_stashed_body() -> None:
    expanded: list[str] = []
    kb = install_output_expand_key_bindings(lambda: True, lambda: "full output", expanded.append)
    handler = next(
        binding.handler
        for binding in kb.bindings
        if tuple(str(k) for k in binding.keys) == ("Keys.ControlO",)
    )

    handler(None)

    assert expanded == ["full output"]


def test_binding_is_gated_on_collapsed_output_being_present() -> None:
    kb = install_output_expand_key_bindings(lambda: False, lambda: "body", lambda _text: None)

    assert all(binding.filter() is False for binding in kb.bindings)


def test_expand_collapsed_output_prints_inline_when_modest(monkeypatch) -> None:
    written: list[str] = []
    monkeypatch.delenv("PAGER", raising=False)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.output_expand.shutil.which",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.output_expand.sys.stdout.write",
        written.append,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.output_expand.sys.stdout.flush", lambda: None
    )

    expand_collapsed_output("hello")

    assert written == ["\n  ↳ expanded\nhello\n"]


def test_expand_collapsed_output_strips_csi_before_inline_write(monkeypatch) -> None:
    written: list[str] = []
    monkeypatch.delenv("PAGER", raising=False)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.output_expand.shutil.which",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.output_expand.sys.stdout.write",
        written.append,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.output_expand.sys.stdout.flush", lambda: None
    )

    expand_collapsed_output("ok\x1b[2J\x1b[Hdone")

    assert written == ["\n  ↳ expanded\nokdone\n"]
    assert "\x1b[" not in written[0]


def test_expand_collapsed_output_uses_pager_when_large(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("PAGER", "less")

    def _fake_run(cmd, input, check):
        calls.append({"cmd": list(cmd), "input": input, "check": check})

    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.hooks.output_expand.subprocess.run",
        _fake_run,
    )
    huge = "x" * (INLINE_EXPAND_MAX_CHARS + 1)
    expand_collapsed_output(huge)

    assert len(calls) == 1
    assert calls[0]["cmd"] == ["less"]  # routed to the pager
    assert isinstance(calls[0]["input"], bytes) and calls[0]["input"]  # content piped in
    assert calls[0]["check"] is False


def test_stash_ring_keeps_last_n_and_ctrl_o_cycles_newest_first() -> None:
    terminal = TerminalSession()
    for i in range(COLLAPSED_OUTPUT_RING_SIZE + 2):
        terminal.stash_collapsed_tool_output(f"peek-{i}")

    assert len(terminal.collapsed_tool_outputs) == COLLAPSED_OUTPUT_RING_SIZE
    assert terminal.collapsed_tool_output == "peek-6"

    seen = [terminal.next_collapsed_output_for_expand() for _ in range(COLLAPSED_OUTPUT_RING_SIZE)]
    assert seen[0] == "peek-6"
    assert seen[1] == "peek-5"
    assert seen[-1] == "peek-2"
    # Wrap back to newest.
    assert terminal.next_collapsed_output_for_expand() == "peek-6"


def test_none_stash_does_not_wipe_earlier_peeks() -> None:
    terminal = TerminalSession()
    terminal.stash_collapsed_tool_output("keep-me")
    terminal.stash_collapsed_tool_output(None)
    assert terminal.has_collapsed_tool_output()
    assert terminal.next_collapsed_output_for_expand() == "keep-me"


def test_collapsed_tool_output_is_ring_newest_only() -> None:
    terminal = TerminalSession()
    terminal.collapsed_tool_output = "via-setter"
    assert terminal.collapsed_tool_outputs == ["via-setter"]
    terminal.collapsed_tool_output = None  # no-op
    assert terminal.collapsed_tool_output == "via-setter"


def test_stash_truncates_unbounded_bodies() -> None:
    terminal = TerminalSession()
    terminal.stash_collapsed_tool_output("x" * (COLLAPSED_STASH_MAX_CHARS + 500))
    body = terminal.collapsed_tool_output or ""
    assert len(body) <= COLLAPSED_STASH_MAX_CHARS
    assert "truncated for Ctrl+O stash" in body
