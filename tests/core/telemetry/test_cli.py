"""Real CLI wiring with fake external boundaries, including saved delivery failure."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from core.telemetry.models import Evidence, Measurement, MetricMapping, Scope
from core.telemetry.storage.store import WatchStore
from integrations.telegram.credentials import TelegramCredentials
from surfaces.cli.commands import triage


class Reader:
    def __init__(self, *_args) -> None:
        self.closed = False

    def verify(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def measure(self, scope: Scope, _mapping: MetricMapping) -> Measurement:
        return Measurement(20, 20, scope.end)

    def evidence(self, _scope: Scope) -> Evidence:
        return {"logs": [], "traces": []}


def test_cli_retains_reports_before_failed_telegram_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations import telegram

    config = tmp_path / "watch.yaml"
    config.write_text("watch:\n  service: payment\n  environment: demo\n", encoding="utf-8")
    state = tmp_path / "watch.db"
    monkeypatch.setattr(triage, "ClickHouseEvidenceReader", Reader)
    monkeypatch.setattr(triage, "resolve_env_credential", lambda _name: "secret")
    monkeypatch.setattr(triage, "investigate", lambda *_: "Observed failure; cause unknown.")
    monkeypatch.setattr(
        telegram,
        "load_credentials_from_env",
        lambda **_kw: TelegramCredentials("123:secret", "42"),
    )
    monkeypatch.setattr(
        telegram, "post_telegram_message", lambda *_args, **_kw: (False, "offline", "")
    )
    runner = CliRunner()
    args = ["watch", "--config", str(config), "--state", str(state), "--once", "--telegram"]
    result = runner.invoke(triage.triage_command, args)
    assert result.exit_code == 0, result.output
    assert "delivery pending" in result.output
    assert not WatchStore(state, None).events()[0]["delivered"]
    result = runner.invoke(triage.triage_command, ["reports", "--state", str(state)])
    assert result.exit_code == 0
    assert "Observed failure; cause unknown." in result.output
    assert "(pending)" in result.output
    # Changing bot identity must not redirect a saved report to a new bot.
    monkeypatch.setattr(
        telegram,
        "load_credentials_from_env",
        lambda **_kw: TelegramCredentials("456:secret", "42"),
    )
    result = runner.invoke(triage.triage_command, args)
    assert result.exit_code != 0 and "different watch" in result.output


def test_repl_reports_delegate_but_continuous_watch_stays_in_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from io import StringIO
    from unittest.mock import MagicMock

    from rich.console import Console

    from surfaces.interactive_shell.command_registry import cli_parity

    calls: list[list[str]] = []

    def run_cli(_console, args, **_kwargs):
        calls.append(args)
        return True

    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    session = MagicMock()
    output = StringIO()
    console = Console(file=output, width=80)
    cli_parity._cmd_triage(session, console, ["reports", "--state", "state.db"])
    cli_parity._cmd_triage(session, console, ["watch"])
    assert calls == [["triage", "reports", "--state", "state.db"]]
    assert "separate terminal" in output.getvalue()
