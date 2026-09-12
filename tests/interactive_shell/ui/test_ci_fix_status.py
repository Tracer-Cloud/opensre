"""Completed repairs update the editable prompt without redrawing scrollback."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text

from config.constants import CI_FIX_LEDGER_PATH_ENV
from integrations.github.tools.ci_fix import ledger
from integrations.github.tools.ci_fix import tool as ci_fix_tool
from surfaces.interactive_shell.runtime.ci_fix_status import bind_ci_fix_status
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import ci_fix_status
from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region
from surfaces.shared.terminal.banner.banner_state import load_launch_status


def test_tool_completion_updates_prompt_from_memory_even_when_save_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CI_FIX_LEDGER_PATH_ENV, str(tmp_path / "ci_fixes.json"))
    session = Session()
    changes: list[int] = []
    counter = ledger.get_ci_fix_counter()
    session.terminal.prompt_refresh_fn = lambda: changes.append(counter.count())
    cleanup = bind_ci_fix_status(session.terminal)
    outcome = {
        "success": True,
        "checks_state": "passed",
        "owner": "example",
        "repo": "service",
        "target_type": "pr",
        "pr_number": 1,
        "source_head_sha": "a" * 40,
    }

    def run_fix(**_kwargs: object) -> dict[str, object]:
        return outcome

    def fail_save(_path: Path, _identities: set[str]) -> set[str]:
        assert changes == [1]
        raise OSError("disk full")

    monkeypatch.setattr(ci_fix_tool, "run_ci_fix", run_fix)
    monkeypatch.setattr(ledger, "append_fix_ids", fail_save)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(ci_fix_tool.fix_github_pr_ci).result(timeout=10) is outcome
        rendered = render_prompt_region(session, ReplState(), SpinnerState())
        text = fragment_list_to_text(to_formatted_text(rendered))
        assert "CI/CD fixes (1) ✓" in text
        assert "Auto (High)" in text
        assert load_launch_status().ci_fix_count == 1
        ci_fix_tool.fix_github_pr_ci()
        assert changes == [1]
    finally:
        cleanup()
    counter.record("b" * 64)
    assert changes == [1]
    assert session.terminal.ci_fix_count_fn is None


def test_zero_chip_is_dim_and_live_line_fits_narrow_terminals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rich.cells import cell_len

    from infrastructure.terminal import theme

    session = Session()
    session.terminal.ci_fix_count_fn = lambda: 0
    for width in (20, 40, 80):
        monkeypatch.setattr(ci_fix_status, "prompt_line_width", lambda width=width: width)
        monkeypatch.setattr(
            "surfaces.interactive_shell.ui.auto_status.prompt_line_width", lambda width=width: width
        )
        rendered = ci_fix_status.prompt_status_ansi(session)
        from prompt_toolkit.formatted_text import ANSI

        plain = fragment_list_to_text(to_formatted_text(ANSI(rendered)))
        assert cell_len(plain) <= width
        assert "✗" not in plain
        if width >= 40:
            assert f"{theme.DIM_ANSI}CI/CD fixes (0)" in rendered
