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


def test_later_schedule_option_replaces_the_earlier_value() -> None:
    parsed, error = _parse_add_args(["--cron", "0 8 * * *", "--time", "09:00"])
    assert parsed is not None and not error
    assert (parsed.cron, parsed.time_text, parsed.cron_selected) == ("", "09:00", False)

    parsed, error = _parse_add_args(["--time", "09:00", "--cron", "0 8 * * *"])
    assert parsed is not None and not error
    assert (parsed.cron, parsed.time_text, parsed.cron_selected) == ("0 8 * * *", "", True)
