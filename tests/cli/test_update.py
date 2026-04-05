from __future__ import annotations

import pytest

from app.cli.update import _is_update_available, run_update


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


def test_check_only_returns_0_when_up_to_date(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")

    rc = run_update(check_only=True)

    assert rc == 0
    assert "already up to date" in capsys.readouterr().out


def test_update_pip_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.update._upgrade_via_pip", lambda: 0)

    rc = run_update(yes=True)

    assert rc == 0
    assert "1.0.0 -> 1.2.3" in capsys.readouterr().out


def test_update_pip_failure_mentions_incomplete_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.update._upgrade_via_pip", lambda: 1)

    rc = run_update(yes=True)

    assert rc == 1
    err = capsys.readouterr().err
    assert "pip upgrade failed" in err
    assert "incomplete" in err


def test_fetch_error_returns_1(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")

    def _raise() -> str:
        raise RuntimeError("network unreachable")

    monkeypatch.setattr("app.cli.update._fetch_latest_version", _raise)

    rc = run_update()

    assert rc == 1
    assert "could not fetch" in capsys.readouterr().err


def test_rate_limit_error_message(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")

    def _raise() -> str:
        raise RuntimeError("GitHub API rate limit exceeded, try again later")

    monkeypatch.setattr("app.cli.update._fetch_latest_version", _raise)

    rc = run_update()

    assert rc == 1
    assert "rate limit" in capsys.readouterr().err


def test_proxy_hint_in_connect_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")

    def _raise() -> str:
        raise RuntimeError("could not connect to GitHub — check your network or HTTPS_PROXY settings")

    monkeypatch.setattr("app.cli.update._fetch_latest_version", _raise)

    rc = run_update()

    assert rc == 1
    assert "HTTPS_PROXY" in capsys.readouterr().err


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


def test_editable_install_prints_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.update._is_editable_install", lambda: True)
    monkeypatch.setattr("app.cli.update._upgrade_via_pip", lambda: 0)

    rc = run_update(yes=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "editable" in out
    assert "1.0.0 -> 1.2.3" in out


def test_binary_install_windows_shows_powershell(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: True)
    monkeypatch.setattr("app.cli.update._is_windows", lambda: True)

    rc = run_update(yes=True)

    assert rc == 1
    assert "iex" in capsys.readouterr().out


def test_binary_install_non_windows_shows_curl(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: True)
    monkeypatch.setattr("app.cli.update._is_windows", lambda: False)

    rc = run_update(yes=True)

    assert rc == 1
    assert "curl" in capsys.readouterr().out


def test_update_prints_release_notes_url_after_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.update._upgrade_via_pip", lambda: 0)

    rc = run_update(yes=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "release notes" in out
    assert "1.2.3" in out


def test_is_update_available_no_downgrade_local_version() -> None:
    assert not _is_update_available("1.0.0+local", "1.0.0")


def test_is_update_available_no_downgrade_dev_version() -> None:
    assert not _is_update_available("0.2.0.dev0", "0.1.3")


def test_is_update_available_when_behind() -> None:
    assert _is_update_available("1.0.0", "1.2.3")


def test_is_update_available_when_equal() -> None:
    assert not _is_update_available("1.0.0", "1.0.0")
