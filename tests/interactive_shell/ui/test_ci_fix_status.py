"""Completed repairs update the editable prompt without redrawing scrollback."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from prompt_toolkit.formatted_text import ANSI, fragment_list_to_text, to_formatted_text

from config.constants import CI_FIX_LEDGER_PATH_ENV
from integrations.github.tools.ci_fix import ledger
from integrations.github.tools.ci_fix import tool as ci_fix_tool
from surfaces.interactive_shell.runtime.ci_fix_status import bind_ci_fix_status
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import ci_fix_status
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
            result = executor.submit(ci_fix_tool.fix_github_pr_ci).result(timeout=10)
        # The tool returns the repair output with its work outcome attached, nothing else changed.
        assert {key: result[key] for key in outcome} == outcome
        assert result["work_outcome"]["status"] == "succeeded"
        rendered = ANSI(ci_fix_status.prompt_status_ansi(session))
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


def test_chip_gives_way_before_the_autonomy_level_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A visible CI chip must never cost the level its last characters.

    The chip-fit budget has to cover the gutter ``auto_status_ansi`` prepends
    and the ellipsis column ``clip_prompt_text`` reserves. Reserving only
    ``len("Auto (High)")`` left widths ~29-35 rendering
    ``Auto (Hi… · CI/CD fixes (0)`` — strictly worse than one column narrower,
    which drops the chip and shows the level in full.
    """
    from config.constants import CI_FIX_COUNT_LABEL

    session = Session()
    for count in (0, 3):
        session.terminal.ci_fix_count_fn = lambda bound=count: bound
        for width in range(20, 60):
            monkeypatch.setattr(ci_fix_status, "prompt_line_width", lambda width=width: width)
            monkeypatch.setattr(
                "surfaces.interactive_shell.ui.auto_status.prompt_line_width",
                lambda width=width: width,
            )
            plain = fragment_list_to_text(
                to_formatted_text(ANSI(ci_fix_status.prompt_status_ansi(session)))
            )
            if CI_FIX_COUNT_LABEL in plain:
                assert "Auto (High)" in plain, f"level truncated beside chip at width {width}"


def test_zero_chip_is_dim_and_live_line_fits_narrow_terminals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rich.cells import cell_len

    from infrastructure.terminal import theme

    session = Session()
    # Degenerate widths exercise the chip-drop fallback; both counts and both
    # quiet states are separate composition branches.
    for count in (0, 3):
        session.terminal.ci_fix_count_fn = lambda bound=count: bound
        for width in (1, 8, 20, 40, 80, 200):
            monkeypatch.setattr(ci_fix_status, "prompt_line_width", lambda width=width: width)
            monkeypatch.setattr(
                "surfaces.interactive_shell.ui.auto_status.prompt_line_width",
                lambda width=width: width,
            )
            for quiet in (False, True):
                rendered = ci_fix_status.prompt_status_ansi(session, quiet=quiet)
                plain = fragment_list_to_text(to_formatted_text(ANSI(rendered)))
                # The row renders under the composer at a pinned height of one.
                # Overflow soft-wraps it into a second row, which drifts
                # prompt_toolkit's row accounting and strands stale chrome on
                # resize — the failure this row's fixed height exists to avoid.
                assert cell_len(plain) <= width
                assert plain == plain.rstrip()
                assert "\n" not in plain
            if count == 0:
                assert "✗" not in plain
                if width >= 40:
                    assert f"{theme.DIM_ANSI}CI/CD fixes (0)" in rendered
