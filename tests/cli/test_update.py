from __future__ import annotations

import signal

import pytest

from infrastructure.process.release_version import (
    development_install_doctor_version_detail,
    extract_main_build_sha,
    extract_main_build_version,
    fetch_latest_version,
    is_update_available,
)
from surfaces.cli.lifecycle.update import _upgrade_via_install_script, run_update


def test_already_up_to_date(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")

    rc = run_update()

    assert rc == 0
    assert "already up to date" in capsys.readouterr().out


def test_check_only_returns_1_when_update_available(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.update._upgrade_via_install_script",
        pytest.fail,
    )

    rc = run_update(check_only=True)

    assert rc == 1
    out = capsys.readouterr().out
    assert "1.0.0" in out
    assert "1.2.3" in out


def test_check_only_returns_0_when_up_to_date(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")

    rc = run_update(check_only=True)

    assert rc == 0
    assert "already up to date" in capsys.readouterr().out


def test_update_install_script_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 0)

    rc = run_update(yes=True)

    assert rc == 0
    assert "1.0.0 -> 1.2.3" in capsys.readouterr().out


def test_update_install_script_failure_shows_retry_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 1)

    rc = run_update(yes=True)

    assert rc == 1
    err = capsys.readouterr().err
    assert "install script failed" in err
    assert "retry manually" in err


def test_fetch_error_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")

    def _raise() -> str:
        raise RuntimeError("network unreachable")

    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", _raise)

    rc = run_update()

    assert rc == 1
    assert "could not fetch" in capsys.readouterr().err


def test_rate_limit_error_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")

    def _raise() -> str:
        raise RuntimeError("GitHub API rate limit exceeded, try again later")

    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", _raise)

    rc = run_update()

    assert rc == 1
    assert "rate limit" in capsys.readouterr().err


def test_proxy_hint_in_connect_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")

    def _raise() -> str:
        raise RuntimeError(
            "could not connect to GitHub — check your network or HTTPS_PROXY settings"
        )

    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", _raise)

    rc = run_update()

    assert rc == 1
    assert "HTTPS_PROXY" in capsys.readouterr().err


def test_binary_install_upgrades_via_install_script(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 0)

    rc = run_update(yes=True)

    assert rc == 0
    assert "1.0.0 -> 1.2.3" in capsys.readouterr().out


def test_editable_install_prints_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_binary_install", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.update.is_editable_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 0)

    rc = run_update(yes=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "editable" in out
    assert "1.0.0 -> 1.2.3" in out


def test_install_script_failure_windows_shows_powershell_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 1)

    rc = run_update(yes=True)

    assert rc == 1
    assert "iex" in capsys.readouterr().err


def test_install_script_failure_unix_shows_curl_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_windows", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 1)

    rc = run_update(yes=True)

    assert rc == 1
    assert "curl" in capsys.readouterr().err


def test_update_prints_main_build_url_after_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_binary_install", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 0)

    rc = run_update(yes=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "main build release" in out
    assert "main-build" in out
    assert "1.2.3" in out


def test_upgrade_via_install_script_uses_main_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure _upgrade_via_install_script installs from the rolling main channel."""
    captured_cmd: list[str] = []

    def fake_run(cmd: list[str], *, check: bool = False, env: dict[str, str] | None = None) -> type:
        captured_cmd.extend(cmd)
        result = type("Result", (), {"returncode": 0})
        return result

    monkeypatch.setattr("surfaces.cli.lifecycle.update.subprocess.run", fake_run)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_windows", lambda: False)

    rc = _upgrade_via_install_script()

    assert rc == 0
    assert captured_cmd == [
        "bash",
        "-c",
        "curl -fsSL https://install.opensre.com | bash -s -- --main",
    ]


def test_extract_main_build_version_from_release_body() -> None:
    body = "## Main build\n\n- Version: `0.1.2026.6.29+main.abc1234`\n- Commit: `abc1234`\n"
    assert extract_main_build_version(body) == "0.1.2026.6.29+main.abc1234"


def test_fetch_latest_version_parses_main_build_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"body": "- Version: `0.1.2026.6.29+main.deadbeef`\n"}

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: FakeResponse())

    assert fetch_latest_version() == "0.1.2026.6.29+main.deadbeef"


def test_is_update_available_no_downgrade_local_version() -> None:
    assert not is_update_available("1.0.0+local", "1.0.0")


def test_is_update_available_no_downgrade_dev_version() -> None:
    assert not is_update_available("0.2.0.dev0", "0.1.3")


def test_is_update_available_when_behind() -> None:
    assert is_update_available("1.0.0", "1.2.3")


def test_is_update_available_when_equal() -> None:
    assert not is_update_available("1.0.0", "1.0.0")


def test_is_update_available_same_day_main_rebuild() -> None:
    current = "0.1.2026.6.29+main.be706ff"
    latest = "0.1.2026.6.29+main.0c306ad"
    assert is_update_available(current, latest)


def test_is_update_available_same_day_main_rebuild_up_to_date() -> None:
    version = "0.1.2026.6.29+main.0c306ad"
    assert not is_update_available(version, version)


def test_is_update_available_feature_branch_is_behind_a_dated_main_build() -> None:
    # A checkout identity must not sort newer than main just because it used to
    # embed today's calendar in the public version.
    assert is_update_available("0.1+feat.sign.in.screen.abc1234", "0.1.2026.8.31+main.deadbee")


def testextract_main_build_sha() -> None:
    assert extract_main_build_sha("0.1.2026.6.29+main.0c306ad") == "0c306ad"
    assert extract_main_build_sha("1.0.0") is None
    assert extract_main_build_sha("1.0.0+local") is None


def test_development_install_doctor_detail_none_for_release_like_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("infrastructure.process.release_version.is_editable_install", lambda: False)
    monkeypatch.delenv("UV_RUN_RECURSION_DEPTH", raising=False)
    assert development_install_doctor_version_detail("2026.4.5") is None


def test_development_install_doctor_detail_editable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("infrastructure.process.release_version.is_editable_install", lambda: True)
    monkeypatch.delenv("UV_RUN_RECURSION_DEPTH", raising=False)
    detail = development_install_doctor_version_detail("2026.4.5")
    assert detail == "2026.4.5 (editable install; skipped comparing to latest main build)"


def test_development_install_doctor_detail_uv_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("infrastructure.process.release_version.is_editable_install", lambda: False)
    monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    detail = development_install_doctor_version_detail("2026.4.5")
    assert detail == "2026.4.5 (uv run; skipped comparing to latest main build)"


def test_container_update_restarts_the_supervisor_without_prompting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A container child confirms nothing and signals the supervisor that installed it."""
    monkeypatch.setenv("OPENSRE_SUPERVISOR_PID", "4242")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 0)
    monkeypatch.setattr("surfaces.cli.lifecycle.update.os.getpid", lambda: 7)
    signaled: list[tuple[int, int]] = []

    def _kill(pid: int, sig: int) -> None:
        signaled.append((pid, sig))

    monkeypatch.setattr("surfaces.cli.lifecycle.update.os.kill", _kill)

    rc = run_update(yes=False)

    assert rc == 0
    assert signaled == [(4242, signal.SIGHUP)]


def test_update_does_not_signal_when_it_is_the_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_SUPERVISOR_PID", "7")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 0)
    monkeypatch.setattr("surfaces.cli.lifecycle.update.os.getpid", lambda: 7)

    def _kill(*_args: object) -> None:
        pytest.fail("signaled")

    monkeypatch.setattr("surfaces.cli.lifecycle.update.os.kill", _kill)

    assert run_update(yes=True) == 0


def test_development_install_doctor_detail_editable_and_uv_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("infrastructure.process.release_version.is_editable_install", lambda: True)
    monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    detail = development_install_doctor_version_detail("2026.4.5")
    assert detail == (
        "2026.4.5 (editable install + uv run; skipped comparing to latest main build)"
    )
