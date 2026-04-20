from __future__ import annotations

import pytest

from app.cli.update import _is_update_available, _upgrade_via_install_script, run_update


def test_already_up_to_date(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
    monkeypatch.setattr("app.cli.update._upgrade_via_install_script", lambda _v: pytest.fail())

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


def test_update_install_script_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._upgrade_via_install_script", lambda _v: 0)

    rc = run_update(yes=True)

    assert rc == 0
    assert "1.0.0 -> 1.2.3" in capsys.readouterr().out


def test_update_install_script_failure_shows_retry_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._upgrade_via_install_script", lambda _v: 1)

    rc = run_update(yes=True)

    assert rc == 1
    err = capsys.readouterr().err
    assert "install script failed" in err
    assert "retry manually" in err


def test_fetch_error_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")

    def _raise() -> str:
        raise RuntimeError("network unreachable")

    monkeypatch.setattr("app.cli.update._fetch_latest_version", _raise)

    rc = run_update()

    assert rc == 1
    assert "could not fetch" in capsys.readouterr().err


def test_rate_limit_error_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")

    def _raise() -> str:
        raise RuntimeError("GitHub API rate limit exceeded, try again later")

    monkeypatch.setattr("app.cli.update._fetch_latest_version", _raise)

    rc = run_update()

    assert rc == 1
    assert "rate limit" in capsys.readouterr().err


def test_proxy_hint_in_connect_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")

    def _raise() -> str:
        raise RuntimeError(
            "could not connect to GitHub — check your network or HTTPS_PROXY settings"
        )

    monkeypatch.setattr("app.cli.update._fetch_latest_version", _raise)

    rc = run_update()

    assert rc == 1
    assert "HTTPS_PROXY" in capsys.readouterr().err


def test_binary_install_upgrades_via_install_script(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: True)
    monkeypatch.setattr("app.cli.update._upgrade_via_install_script", lambda _v: 0)

    rc = run_update(yes=True)

    assert rc == 0
    assert "1.0.0 -> 1.2.3" in capsys.readouterr().out


def test_editable_install_prints_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.update._is_editable_install", lambda: True)
    monkeypatch.setattr("app.cli.update._upgrade_via_install_script", lambda _v: 0)

    rc = run_update(yes=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "editable" in out
    assert "1.0.0 -> 1.2.3" in out


def test_install_script_failure_windows_shows_powershell_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_windows", lambda: True)
    monkeypatch.setattr("app.cli.update._upgrade_via_install_script", lambda _v: 1)

    rc = run_update(yes=True)

    assert rc == 1
    assert "iex" in capsys.readouterr().err


def test_install_script_failure_unix_shows_curl_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_windows", lambda: False)
    monkeypatch.setattr("app.cli.update._upgrade_via_install_script", lambda _v: 1)

    rc = run_update(yes=True)

    assert rc == 1
    assert "curl" in capsys.readouterr().err


def test_update_prints_release_notes_url_after_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("app.cli.update.get_version", lambda: "1.0.0")
    monkeypatch.setattr("app.cli.update._fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("app.cli.update._is_binary_install", lambda: False)
    monkeypatch.setattr("app.cli.update._upgrade_via_install_script", lambda _v: 0)

    rc = run_update(yes=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "release notes" in out
    assert "1.2.3" in out


def test_upgrade_via_install_script_passes_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure _upgrade_via_install_script forwards the target version to the subprocess."""
    captured_env: dict[str, str] = {}

    def fake_run(cmd: list[str], *, check: bool = False, env: dict[str, str] | None = None) -> type:
        if env:
            captured_env.update(env)
        result = type("Result", (), {"returncode": 0})
        return result

    monkeypatch.setattr("app.cli.update.subprocess.run", fake_run)
    monkeypatch.setattr("app.cli.update._is_windows", lambda: False)

    rc = _upgrade_via_install_script("2026.4.5")

    assert rc == 0
    assert captured_env.get("OPENSRE_VERSION") == "2026.4.5"


def test_is_update_available_no_downgrade_local_version() -> None:
    assert not _is_update_available("1.0.0+local", "1.0.0")


def test_is_update_available_no_downgrade_dev_version() -> None:
    assert not _is_update_available("0.2.0.dev0", "0.1.3")


def test_is_update_available_when_behind() -> None:
    assert _is_update_available("1.0.0", "1.2.3")


def test_is_update_available_when_equal() -> None:
    assert not _is_update_available("1.0.0", "1.0.0")
