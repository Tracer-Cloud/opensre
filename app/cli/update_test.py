from __future__ import annotations

import pytest

from app.cli.update import run_update


def test_already_up_to_date(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")

    rc = run_update()

    assert rc == 0
    assert "already up to date" in capsys.readouterr().out


def test_check_only_returns_1_when_update_available(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._upgrade_via_pip", pytest.fail)

    rc = run_update(check_only=True)

    assert rc == 1
    out = capsys.readouterr().out
    assert "1.0.0" in out
    assert "1.2.3" in out


def test_update_pip_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.update._upgrade_via_pip", lambda: 0)

    rc = run_update(yes=True)

    assert rc == 0
    assert "1.0.0 -> 1.2.3" in capsys.readouterr().out


def test_update_pip_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.update._upgrade_via_pip", lambda: 1)

    rc = run_update(yes=True)

    assert rc == 1
    assert "pip upgrade failed" in capsys.readouterr().err


def test_fetch_error_returns_1(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")

    def _raise() -> str:
        raise RuntimeError("network unreachable")

    monkeypatch.setattr("app.cli.update._fetch_latest_version", _raise)

    rc = run_update()

    assert rc == 1
    assert "could not fetch" in capsys.readouterr().err


def test_binary_install_prints_instructions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: True)
    monkeypatch.setattr("app.cli.update._upgrade_via_pip", pytest.fail)

    rc = run_update(yes=True)

    assert rc == 1
    assert "install script" in capsys.readouterr().out
