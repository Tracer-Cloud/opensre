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
    from integrations.telegram import credentials, delivery

    config = tmp_path / "watch.yaml"
    config.write_text("watch:\n  service: payment\n  environment: demo\n", encoding="utf-8")
    state = tmp_path / "watch.db"
    monkeypatch.setattr(triage, "ClickHouseEvidenceReader", Reader)
    monkeypatch.setattr(triage, "resolve_env_credential", lambda _name: "secret")
    monkeypatch.setattr(triage, "investigate", lambda *_: "Observed failure; cause unknown.")
    monkeypatch.setattr(
        credentials,
        "load_credentials_from_env",
        lambda **_kw: TelegramCredentials("123:secret", "42"),
    )
    monkeypatch.setattr(
        delivery, "post_telegram_message", lambda *_args, **_kw: (False, "offline", "")
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
        credentials,
        "load_credentials_from_env",
        lambda **_kw: TelegramCredentials("456:secret", "42"),
    )
    result = runner.invoke(triage.triage_command, args)
    assert result.exit_code != 0 and "different watch" in result.output
