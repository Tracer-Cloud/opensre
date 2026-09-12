"""Argument parsing for /loops add, including the tick mode flag."""

from __future__ import annotations

from surfaces.interactive_shell.command_registry.loops_cmds import _parse_add_args


def test_mode_flag_is_parsed() -> None:
    parsed, error = _parse_add_args(
        [
            "--name",
            "CI",
            "fix",
            "agent",
            "--cron",
            "*/2 * * * *",
            "--mode",
            "agent",
            "--prompt",
            "repair",
            "failing",
            "checks",
        ]
    )

    assert error == ""
    assert parsed is not None
    assert parsed.mode == "agent"
    assert parsed.name == "CI fix agent"
    assert parsed.prompt == "repair failing checks"


def test_mode_defaults_to_empty_for_report_loops() -> None:
    parsed, error = _parse_add_args(["--name", "x", "--time", "08:30", "--prompt", "p"])

    assert error == ""
    assert parsed is not None
    assert parsed.mode == ""


def test_mode_requires_a_value() -> None:
    parsed, error = _parse_add_args(["--mode", "--prompt", "p"])

    assert parsed is None
    assert error == "--mode requires a value"
