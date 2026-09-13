from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import surfaces.cli.lifecycle.windows.powershell as windows_powershell
from config.constants.installer import (
    OPENSRE_INSTALL_CHANNEL_ENV,
    OPENSRE_INSTALL_DIR_ENV,
    OPENSRE_UPDATE_EXECUTABLE_ENV,
    OPENSRE_UPDATE_PARENT_PID_ENV,
    OPENSRE_UPDATE_PARENT_STARTED_ENV,
    OPENSRE_VERSION_ENV,
    POWERSHELL_MODULE_PATH_ENV,
)
from infrastructure.process.release_version import (
    development_install_doctor_version_detail,
    extract_main_build_sha,
    extract_main_build_version,
    fetch_latest_version,
    is_update_available,
)
from surfaces.cli.lifecycle.update import _upgrade_via_install_script, run_update
from surfaces.cli.lifecycle.windows.layout import (
    MalformedWindowsInstallError,
    WindowsBinaryInstall,
)
from surfaces.cli.lifecycle.windows.processes import WindowsProcessIdentity


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


def test_windows_upgrade_passes_running_process_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_cmd: list[str] = []
    captured_env: dict[str, str] = {}
    powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    executable = Path(r"C:\Program Files\OpenSRE\.opensre-app\versions\build-1\opensre.exe")

    def fake_run(cmd: list[str], *, check: bool = False, env: dict[str, str] | None = None) -> type:
        captured_cmd.extend(cmd)
        captured_env.update(env or {})
        return type("Result", (), {"returncode": 0})

    def fake_process_identity(
        pid: int, *, expected_executable: Path | None = None
    ) -> tuple[WindowsProcessIdentity, None]:
        assert pid == 5844
        assert expected_executable == executable
        return WindowsProcessIdentity(pid, executable, 133_801_632_000_000_000), None

    monkeypatch.setattr("surfaces.cli.lifecycle.update.subprocess.run", fake_run)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_binary_install", lambda: True)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.powershell.windows_powershell_executable",
        lambda: powershell,
    )
    monkeypatch.setattr("surfaces.cli.lifecycle.update.os.getpid", lambda: 5844)
    monkeypatch.setattr("surfaces.cli.lifecycle.update.sys.executable", str(executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.update.windows_process_identity", fake_process_identity
    )
    monkeypatch.setenv("OPENSRE_TEST_PARENT_VALUE", "preserved")

    rc = _upgrade_via_install_script()

    assert rc == 0
    assert captured_cmd[:7] == [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
    ]
    assert "$PSVersionTable.PSEdition" in captured_cmd[7]
    assert "PowerShell 5.1" in captured_cmd[7]
    assert "OPENSRE_INSTALL_CHANNEL='main'" in captured_cmd[7]
    assert captured_env["OPENSRE_UPDATE_PARENT_PID"] == "5844"
    assert captured_env["OPENSRE_UPDATE_EXECUTABLE"] == str(executable)
    assert captured_env[OPENSRE_UPDATE_PARENT_STARTED_ENV] == "133801632000000000"
    assert captured_env["OPENSRE_AUTO_LAUNCH"] == "0"
    assert captured_env["OPENSRE_TEST_PARENT_VALUE"] == "preserved"


def test_windows_binary_upgrade_fails_closed_without_process_identity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("installer must not run without a verified parent identity")

    monkeypatch.setattr("surfaces.cli.lifecycle.update.subprocess.run", fail_run)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_binary_install", lambda: True)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.update.windows_process_identity",
        lambda *_args, **_kwargs: (None, "access denied"),
    )

    assert _upgrade_via_install_script() == 1
    assert "could not verify running OpenSRE process identity" in capsys.readouterr().err


def test_windows_upgrade_drops_inherited_powershell_module_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_cmd: list[str] = []
    captured_env: dict[str, str] = {}

    def fake_run(cmd: list[str], *, check: bool = False, env: dict[str, str] | None = None) -> type:
        captured_cmd.extend(cmd)
        captured_env.update(env or {})
        return type("Result", (), {"returncode": 0})

    powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    monkeypatch.setattr("surfaces.cli.lifecycle.update.subprocess.run", fake_run)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_binary_install", lambda: False)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.powershell.windows_powershell_executable",
        lambda: powershell,
    )
    monkeypatch.setenv(POWERSHELL_MODULE_PATH_ENV.upper(), r"C:\Program Files\PowerShell\7\Modules")
    monkeypatch.setenv("OPENSRE_TEST_PARENT_VALUE", "preserved")

    rc = _upgrade_via_install_script()

    assert rc == 0
    assert captured_cmd[0] == powershell
    assert not any(
        name.casefold() == POWERSHELL_MODULE_PATH_ENV.casefold() for name in captured_env
    )
    assert os.environ[POWERSHELL_MODULE_PATH_ENV.upper()] == (
        r"C:\Program Files\PowerShell\7\Modules"
    )
    assert captured_env["OPENSRE_TEST_PARENT_VALUE"] == "preserved"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell regression")
def test_windows_powershell_environment_resolves_installer_cmdlets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(POWERSHELL_MODULE_PATH_ENV, r"C:\Program Files\PowerShell\7\Modules")
    fake_system_root = tmp_path / "spoofed Windows"
    fake_powershell = (
        fake_system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    fake_powershell.parent.mkdir(parents=True)
    fake_powershell.write_bytes(b"not PowerShell")
    monkeypatch.setenv("SYSTEMROOT", str(fake_system_root))
    monkeypatch.setenv("WINDIR", str(fake_system_root))

    executable = windows_powershell.windows_powershell_executable()
    assert Path(executable) != fake_powershell

    completed = subprocess.run(
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            windows_powershell.windows_powershell_51_script(
                "Write-Output $env:PSModulePath; "
                "Get-Command Get-FileHash,Expand-Archive | Select-Object -ExpandProperty Name"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=windows_powershell.windows_powershell_environment(),
    )

    assert completed.returncode == 0, completed.stderr
    assert r"WindowsPowerShell\v1.0\Modules" in completed.stdout
    assert {"Get-FileHash", "Expand-Archive"}.issubset(set(completed.stdout.splitlines()))


def test_windows_non_binary_upgrade_omits_binary_process_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_env: dict[str, str] = {}

    def fake_run(cmd: list[str], *, check: bool = False, env: dict[str, str] | None = None) -> type:
        captured_env.update(env or {})
        return type("Result", (), {"returncode": 0})

    monkeypatch.setattr("surfaces.cli.lifecycle.update.subprocess.run", fake_run)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_binary_install", lambda: False)
    monkeypatch.setenv("OPENSRE_UPDATE_PARENT_PID", "stale-parent")
    monkeypatch.setenv("OPENSRE_UPDATE_EXECUTABLE", r"C:\stale\opensre.exe")
    monkeypatch.setenv(OPENSRE_UPDATE_PARENT_STARTED_ENV, "stale-start")

    rc = _upgrade_via_install_script()

    assert rc == 0
    assert captured_env["OPENSRE_AUTO_LAUNCH"] == "0"
    assert "OPENSRE_UPDATE_PARENT_PID" not in captured_env
    assert "OPENSRE_UPDATE_EXECUTABLE" not in captured_env
    assert OPENSRE_UPDATE_PARENT_STARTED_ENV not in captured_env


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


def test_development_install_doctor_detail_editable_and_uv_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("infrastructure.process.release_version.is_editable_install", lambda: True)
    monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    detail = development_install_doctor_version_detail("2026.4.5")
    assert detail == (
        "2026.4.5 (editable install + uv run; skipped comparing to latest main build)"
    )


def test_windows_retry_hint_omits_unreproducible_process_handoff(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The manual retry must not expose process-identity values a user cannot reproduce."""
    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_binary_install", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 1)

    rc = run_update(yes=True)

    assert rc == 1
    err = capsys.readouterr().err
    assert "open a new Windows PowerShell window and run" in err
    assert f'$env:{POWERSHELL_MODULE_PATH_ENV}="$PSHOME\\Modules"' in err
    assert f"$env:{OPENSRE_INSTALL_CHANNEL_ENV}='main'" in err
    assert f"Remove-Item Env:{OPENSRE_VERSION_ENV} -ErrorAction SilentlyContinue" in err
    assert "irm https://install.opensre.com | iex" in err
    assert OPENSRE_UPDATE_EXECUTABLE_ENV not in err
    assert OPENSRE_UPDATE_PARENT_PID_ENV not in err


@pytest.mark.parametrize("managed", [False, True], ids=["legacy-onefile", "managed-onedir"])
def test_windows_retry_hint_preserves_custom_binary_install_directory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    *,
    managed: bool,
) -> None:
    install_dir = tmp_path / "O'Brien custom install"
    executable = install_dir / ".opensre-app" / "versions" / "build-1" / "opensre.exe"
    app_root = install_dir / ".opensre-app" if managed else None
    if not managed:
        executable = install_dir / "opensre.exe"
    installation = WindowsBinaryInstall(
        executable=executable,
        app_root=app_root,
        launcher=None,
        paths=(executable,),
    )

    def _classify(_executable: Path) -> WindowsBinaryInstall:
        return installation

    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 1)
    monkeypatch.setattr("surfaces.cli.lifecycle.update.classify_windows_binary_install", _classify)

    rc = run_update(yes=True)

    assert rc == 1
    escaped_install_dir = str(install_dir).replace("'", "''")
    err = capsys.readouterr().err
    assert f"$env:{OPENSRE_INSTALL_DIR_ENV}='{escaped_install_dir}'" in err
    assert f"Remove-Item Env:{OPENSRE_VERSION_ENV} -ErrorAction SilentlyContinue" in err
    assert "irm https://install.opensre.com | iex" in err
    assert OPENSRE_UPDATE_EXECUTABLE_ENV not in err
    assert OPENSRE_UPDATE_PARENT_PID_ENV not in err


def test_windows_retry_hint_falls_back_for_malformed_binary_layout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _raise_malformed(_executable: Path) -> WindowsBinaryInstall:
        raise MalformedWindowsInstallError("invalid marker")

    monkeypatch.setattr("surfaces.cli.lifecycle.update.get_opensre_version", lambda: "1.0.0")
    monkeypatch.setattr("surfaces.cli.lifecycle.update.fetch_latest_version", lambda: "1.2.3")
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.update._upgrade_via_install_script", lambda: 1)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.update.classify_windows_binary_install", _raise_malformed
    )

    rc = run_update(yes=True)

    assert rc == 1
    err = capsys.readouterr().err
    assert f"$env:{OPENSRE_INSTALL_DIR_ENV}" not in err
    assert f"$env:{OPENSRE_INSTALL_CHANNEL_ENV}='main'" in err
    assert f"Remove-Item Env:{OPENSRE_VERSION_ENV} -ErrorAction SilentlyContinue" in err
    assert "irm https://install.opensre.com | iex" in err
