"""Coverage for the ``python -m surfaces.cli.wizard`` entrypoint."""

from __future__ import annotations

import click
import pytest

from infrastructure.analytics.events import cli_command_event_name
from surfaces.cli.wizard import app as wizard_main


def test_main_initialises_sentry_and_emits_cli_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_calls: list[int] = []
    captured: list[dict[str, object] | None] = []

    monkeypatch.setattr(wizard_main, "init_sentry", lambda **_kw: init_calls.append(1))
    monkeypatch.setattr(wizard_main, "shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr(
        wizard_main,
        "capture_first_run_if_needed",
        lambda: captured.append({"_marker": "install"}),
    )
    monkeypatch.setattr(
        wizard_main,
        "capture_cli_invoked",
        lambda properties=None, command_parts=(): captured.append(
            {**(properties or {}), "event_name": cli_command_event_name(command_parts)}
        ),
    )
    monkeypatch.setattr(wizard_main, "run_wizard", lambda: 0)
    monkeypatch.setattr(wizard_main, "install_questionary_escape_cancel", lambda: None)

    exit_code = wizard_main.main()

    assert exit_code == 0
    assert init_calls == [1]
    install_marker, properties = captured
    assert install_marker == {"_marker": "install"}
    assert properties is not None
    assert properties["entrypoint"] == "python -m surfaces.cli.wizard"
    assert properties["command_path"] == "python -m surfaces.cli.wizard wizard"
    assert properties["command_family"] == "wizard"
    assert properties["event_name"] == "cli_command_opensre_onboard"


def test_main_shuts_down_analytics_without_blocking_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flush_calls: list[bool] = []

    monkeypatch.setattr(wizard_main, "init_sentry", lambda **_kw: None)
    monkeypatch.setattr(wizard_main, "capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr(wizard_main, "capture_cli_invoked", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        wizard_main,
        "shutdown_analytics",
        lambda **kw: flush_calls.append(bool(kw.get("flush"))),
    )
    monkeypatch.setattr(wizard_main, "install_questionary_escape_cancel", lambda: None)

    def _raise() -> int:
        raise RuntimeError("wizard exploded")

    monkeypatch.setattr(wizard_main, "run_wizard", _raise)

    with pytest.raises(RuntimeError):
        wizard_main.main()

    assert flush_calls == [False]


def test_main_treats_abort_as_clean_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flush_calls: list[bool] = []

    monkeypatch.setattr(wizard_main, "init_sentry", lambda **_kw: None)
    monkeypatch.setattr(wizard_main, "capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr(wizard_main, "capture_cli_invoked", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        wizard_main,
        "shutdown_analytics",
        lambda **kw: flush_calls.append(bool(kw.get("flush"))),
    )
    monkeypatch.setattr(wizard_main, "install_questionary_escape_cancel", lambda: None)
    monkeypatch.setattr(
        wizard_main,
        "run_wizard",
        lambda: (_ for _ in ()).throw(click.Abort()),
    )

    exit_code = wizard_main.main()

    assert exit_code == 0
    assert flush_calls == [False]


def test_main_treats_keyboard_interrupt_as_clean_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flush_calls: list[bool] = []

    monkeypatch.setattr(wizard_main, "init_sentry", lambda **_kw: None)
    monkeypatch.setattr(wizard_main, "capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr(wizard_main, "capture_cli_invoked", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        wizard_main,
        "shutdown_analytics",
        lambda **kw: flush_calls.append(bool(kw.get("flush"))),
    )
    monkeypatch.setattr(wizard_main, "install_questionary_escape_cancel", lambda: None)
    monkeypatch.setattr(
        wizard_main,
        "run_wizard",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    exit_code = wizard_main.main()

    assert exit_code == 0
    assert flush_calls == [False]
