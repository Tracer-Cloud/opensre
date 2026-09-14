"""Tests for the interactive-shell launch banner."""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from config.constants import PRODUCT_DISPLAY_NAME
from surfaces.interactive_shell.ui import poster as poster_module
from surfaces.shared.terminal.banner import banner as banner_module
from surfaces.shared.terminal.banner import banner_state as banner_state_module
from surfaces.shared.terminal.banner.banner_state import LaunchStatus


def _rgb(hex_color: str) -> str:
    """``"#RRGGBB"`` → the ``"r;g;b"`` truecolor triple."""
    h = hex_color.lstrip("#")
    return f"{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}"


def _fixed_status() -> LaunchStatus:
    return LaunchStatus(
        skill_count=21,
        ci_fix_count=2,
    )


def _line_centers(plain: str, *, width: int) -> list[tuple[str, int, int]]:
    """Return (stripped, leading_spaces, trailing_pad) for non-empty lines."""
    rows: list[tuple[str, int, int]] = []
    for raw in plain.splitlines():
        if not raw.strip():
            continue
        stripped = raw.rstrip("\n")
        # Console may pad to width with trailing spaces.
        content = stripped.rstrip()
        lead = len(content) - len(content.lstrip())
        body = content.strip()
        trail = width - lead - len(body) if width >= lead + len(body) else 0
        rows.append((body, lead, trail))
    return rows


def test_launch_banner_is_borderless_centered_hero(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    monkeypatch.setattr(banner_module, "get_opensre_version", lambda: "0.1.2026.9.2+main.abc1234")
    console_file = io.StringIO()
    console = Console(file=console_file, force_terminal=False, highlight=False, width=120)

    banner_module.render_launch_banner(console)

    output = console_file.getvalue()
    assert PRODUCT_DISPLAY_NAME == "OpenSRE"
    assert "OpenSRE" in output or "██████" in output  # wordmark or compact title
    assert "v0.1.2026.9.2+main.abc1234" in output  # full build version
    assert "Skills (21) ✓" in output
    assert "CI/CD fixes (2) ✓" in output
    # Welcome title + product description (same copy as the sign-in screen),
    # in place of the old TIP line.
    assert "Welcome to OpenSRE CLI" in output
    assert "AI-powered DevOps agent" in output
    # Banner is install health, not a shortcut dump (those live on ``?``).
    assert "/ commands" not in output
    assert "Enter send" not in output
    # Only the two capability items — no MCPs or AGENTS.md line.
    assert "MCPs" not in output
    assert "AGENTS.md" not in output
    assert not any(char in output for char in "╭╮╰╯│")


def test_launch_banner_centers_each_row_independently(monkeypatch: object) -> None:
    """Short rows (version) must share the same center axis as the wordmark.

    Bundling unequal lines into one ``Align.center`` left-aligns shorts inside
    the widest line — the school-project look vs Droid.
    """
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    monkeypatch.setattr(banner_module, "get_opensre_version", lambda: "0.1.2026.9.2+main.abc1234")
    width = 120
    console = Console(record=True, force_terminal=False, highlight=False, width=width)
    console.print(banner_module.build_launch_banner(console))
    plain = console.export_text(styles=False)
    rows = _line_centers(plain, width=width)
    assert rows, "banner must paint at least one row"

    def _center_col(body: str, lead: int) -> float:
        return lead + (len(body) / 2)

    centers = [_center_col(body, lead) for body, lead, _trail in rows]
    mid = width / 2
    # Every content row's midpoint should sit near the terminal midline.
    for body, center in zip([r[0] for r in rows], centers, strict=True):
        assert abs(center - mid) <= 2.0, (body, center, mid)


def test_launch_banner_draws_ring_logo_on_wide_terminals(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    console = Console(record=True, force_terminal=False, highlight=False, width=120)

    console.print(banner_module.build_launch_banner(console))

    output = console.export_text(styles=False)
    # A ring-wall row of the braille "loops" mark.
    assert "⣿⣿⠀⠀⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⠀⢸⣿⣿" in output


def test_wordmark_spin_frames_complete_a_full_revolution() -> None:
    frames = banner_module.build_wordmark_spin_frames()
    blank = "\u2800"

    assert len(frames) > 10
    assert frames[0].rows == frames[-1].rows
    assert any(frame.back_facing for frame in frames)
    assert all(tuple(map(len, frame.rows)) == tuple(map(len, frames[0].rows)) for frame in frames)
    edge = min(frames, key=lambda frame: frame.scale)
    assert max(len(row.strip(blank)) for row in edge.rows) < max(
        len(row.strip(blank)) for row in frames[0].rows
    )


def test_launch_banner_spins_once_in_place_on_tty(monkeypatch: object) -> None:
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
    monkeypatch.setattr(banner_module.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    console = Console(
        file=fake_stdout,
        force_terminal=True,
        highlight=False,
        color_system="truecolor",
        width=120,
    )

    banner_module.render_launch_banner(console)

    written = "".join(fake_stdout.writes)
    animation_end = written.index("\x1b[?25h") + len("\x1b[?25h")
    animation = written[:animation_end]
    assert written.count("\x1b[10A") > 10
    assert "\x1b[2;1H" not in animation
    assert "\x1b[H" not in animation
    assert "\n" not in animation.replace("\r\n", "")
    assert written.index("\x1b[?25l") < written.index("\x1b[1G")
    assert written.index("\x1b[?25h") < written.index("Welcome to OpenSRE CLI")


def test_launch_banner_falls_back_to_title_on_narrow_terminals(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    console = Console(record=True, force_terminal=False, highlight=False, width=20)

    console.print(banner_module.build_launch_banner(console))

    output = console.export_text(styles=False)
    assert "OpenSRE" in output
    assert "⣿⣿" not in output  # braille ring omitted below its min width


def test_launch_banner_uses_active_theme_palette(monkeypatch: object) -> None:
    from infrastructure.terminal.theme import THEME_REGISTRY, set_active_theme

    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    set_active_theme("pink")
    pink_rgb = _rgb(THEME_REGISTRY["pink"].HIGHLIGHT)
    green_rgb = _rgb(THEME_REGISTRY["green"].HIGHLIGHT)

    console = Console(record=True, width=120)
    console.print(banner_module.build_launch_banner(console))

    styled = console.export_text(styles=True)
    assert pink_rgb in styled
    assert green_rgb not in styled


def test_launch_banner_stacks_without_overflow_on_narrow_terminals(
    monkeypatch: object,
) -> None:
    from rich.cells import cell_len

    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    for width in (80, 50, 36):
        console = Console(record=True, force_terminal=False, highlight=False, width=width)
        banner_module.render_launch_banner(console)
        plain = console.export_text(styles=False)
        assert all(cell_len(line.rstrip()) <= width for line in plain.splitlines())


def test_status_marks_empty_or_unavailable_capabilities(monkeypatch: object) -> None:
    monkeypatch.setattr(
        banner_module,
        "load_launch_status",
        lambda: LaunchStatus(
            skill_count=0,
            ci_fix_count=0,
        ),
    )
    console = Console(record=True, force_terminal=False, highlight=False, width=120)

    console.print(banner_module.build_launch_banner(console))

    output = console.export_text(styles=False)
    assert "Skills (0) ✗" in output
    assert "CI/CD fixes (0)" in output
    assert "✗" not in output.split("CI/CD fixes (0)", 1)[1]


def test_banner_reads_ledger_and_unavailable_probes_are_zero(
    monkeypatch: object, tmp_path: Path
) -> None:
    import json

    from config.constants import CI_FIX_LEDGER_PATH_ENV

    ledger = tmp_path / "ci_fixes.json"
    ledger.write_text(json.dumps({"version": 1, "fixes": ["a" * 64, "b" * 64]}))
    monkeypatch.setenv(CI_FIX_LEDGER_PATH_ENV, str(ledger))
    assert banner_state_module.load_launch_status().ci_fix_count == 2

    broken = tmp_path / "broken.json"
    broken.write_text("{broken")
    monkeypatch.setenv(CI_FIX_LEDGER_PATH_ENV, str(broken))
    monkeypatch.setattr(banner_state_module, "_BUNDLED_SKILLS_DIR", tmp_path / "missing-skills")
    assert banner_state_module.load_launch_status() == LaunchStatus(skill_count=0, ci_fix_count=0)
    monkeypatch.setenv(CI_FIX_LEDGER_PATH_ENV, str(tmp_path / "missing.json"))
    assert banner_state_module.load_launch_status().ci_fix_count == 0


def test_banner_shows_the_full_build_version(monkeypatch: object) -> None:
    monkeypatch.setattr(banner_module, "load_launch_status", _fixed_status)
    monkeypatch.setattr(
        banner_module,
        "get_opensre_version",
        lambda: "0.1.2026.9.2+main.abc1234",
    )
    # Full build version (identity for support / bug reports), not a trimmed one.
    version = banner_module._build_version_line().plain
    assert version == "v0.1.2026.9.2+main.abc1234"


def test_refresh_welcome_poster_uses_repl_safe_render(monkeypatch: object) -> None:
    console = Console(record=True, width=120)
    render_calls: list[dict[str, object | None]] = []

    monkeypatch.setattr(
        "surfaces.shared.terminal.components.rendering.repl_clear_screen",
        lambda: None,
    )

    def _fake_render(
        _console: Console,
        *,
        session: object = None,
        theme_notice: str | None = None,
    ) -> None:
        render_calls.append({"session": session, "theme_notice": theme_notice})

    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.poster.repl_render_launch_poster",
        _fake_render,
    )

    poster_module.refresh_welcome_poster(console, session="sess", theme_notice="pink")

    assert render_calls == [{"session": "sess", "theme_notice": "pink"}]
