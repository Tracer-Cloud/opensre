"""Coverage for the ``python -m integrations`` entrypoint.

Mirrors the contract from ``tests/cli/test_main.py`` for the standalone
integrations CLI: Sentry must be initialised, accepted commands must emit a
single command-specific invocation event with command metadata, ``--help`` and unknown
commands must skip analytics.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from infrastructure.analytics.events import cli_command_event_name
from integrations import app as integrations_main


@pytest.fixture(autouse=True)
def _stub_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(integrations_main, "init_sentry", lambda **_kw: None)
    monkeypatch.setattr(integrations_main, "shutdown_analytics", lambda **_kw: None)


def _captures(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object] | None]:
    captured: list[dict[str, object] | None] = []
    monkeypatch.setattr(
        integrations_main,
        "capture_first_run_if_needed",
        lambda: captured.append({"_marker": "install"}),
    )
    monkeypatch.setattr(
        integrations_main,
        "capture_cli_invoked",
        lambda properties=None, command_parts=(): captured.append(
            {**(properties or {}), "event_name": cli_command_event_name(command_parts)}
        ),
    )
    return captured


def test_help_does_not_capture_analytics(monkeypatch, capsys) -> None:
    captured = _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m integrations", "--help"])

    integrations_main.main()

    assert captured == []
    assert "Commands: setup, list, show, remove, verify" in capsys.readouterr().out


def test_unknown_command_does_not_capture_analytics(monkeypatch, capsys) -> None:
    captured = _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m integrations", "bogus"])

    with pytest.raises(SystemExit) as excinfo:
        integrations_main.main()

    assert excinfo.value.code == 1
    assert captured == []
    assert "Unknown command" in capsys.readouterr().err


def test_list_emits_cli_invoked_with_metadata(monkeypatch) -> None:
    captured = _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m integrations", "list"])

    with patch.object(integrations_main, "cmd_list") as cmd_list:
        integrations_main.main()

    cmd_list.assert_called_once()
    install_marker, properties = captured
    assert install_marker == {"_marker": "install"}
    assert properties is not None
    assert properties["entrypoint"] == "python -m integrations"
    assert properties["command_path"] == "python -m integrations list"
    assert properties["command_family"] == "list"
    assert properties["command_leaf"] == "list"
    assert properties["event_name"] == "cli_command_opensre_integrations_list"
    assert "subcommand" not in properties


def test_verify_excludes_service_operands_from_command_metadata(monkeypatch) -> None:
    captured = _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m integrations", "verify", "slack"])

    with (
        patch.object(integrations_main, "cmd_verify", return_value=0) as cmd_verify,
        pytest.raises(SystemExit) as excinfo,
    ):
        integrations_main.main()

    assert excinfo.value.code == 0
    cmd_verify.assert_called_once_with("slack", send_slack_test=False)
    install_marker, properties = captured
    assert install_marker == {"_marker": "install"}
    assert properties is not None
    assert properties["command_path"] == "python -m integrations verify"
    assert properties["command_family"] == "verify"
    assert "subcommand" not in properties
    assert properties["command_leaf"] == "verify"
    assert properties["event_name"] == "cli_command_opensre_integrations_verify"


def test_main_initialises_sentry(monkeypatch) -> None:
    init_calls: list[int] = []
    monkeypatch.setattr(integrations_main, "init_sentry", lambda **_kw: init_calls.append(1))
    _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m integrations", "--help"])

    integrations_main.main()

    assert init_calls == [1]


def test_command_dispatch_table_is_the_known_set() -> None:
    """Unknown-command help and handlers share one map — no parallel if-chain."""
    assert tuple(integrations_main._COMMANDS) == (
        "setup",
        "list",
        "show",
        "remove",
        "verify",
    )


def test_setup_dispatches_through_table_and_may_verify(monkeypatch) -> None:
    captured = _captures(monkeypatch)
    monkeypatch.setattr("sys.argv", ["python -m integrations", "setup", "slack"])

    with (
        patch.object(integrations_main, "cmd_setup", return_value="slack") as cmd_setup,
        patch.object(integrations_main, "cmd_verify", return_value=0) as cmd_verify,
        patch.object(
            integrations_main,
            "SUPPORTED_VERIFY_SERVICES",
            frozenset({"slack"}),
        ),
        pytest.raises(SystemExit) as excinfo,
    ):
        integrations_main.main()

    assert excinfo.value.code == 0
    cmd_setup.assert_called_once_with("slack")
    cmd_verify.assert_called_once_with("slack")
    assert captured  # analytics still fired before the handler ran
