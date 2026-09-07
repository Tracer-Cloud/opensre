"""Tests for the per-user background scheduler service."""

from __future__ import annotations

import plistlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler import background_service as svc


class _Runner:
    """Records OS commands; ``failing`` names a command word that exits 1, ``loaded`` makes
    the post-removal state check report the service as still running."""

    def __init__(self, failing: str = "", loaded: bool = False) -> None:
        self.commands: list[list[str]] = []
        self._failing = failing
        self._loaded = loaded

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        self.commands.append(argv)
        if argv[:2] == ["launchctl", "print"] or argv[2:3] == ["is-active"]:
            return subprocess.CompletedProcess(argv, 0 if self._loaded else 3, "", "")
        code = 1 if self._failing and self._failing in argv else 0
        return subprocess.CompletedProcess(argv, code, "", "denied" if code else "")


def test_macos_install_writes_a_keepalive_launch_agent_and_bootstraps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    runner = _Runner()

    # Act
    state = svc.install_background_service(
        home=tmp_path,
        system="Darwin",
        run=runner,
        command=["/usr/local/bin/opensre", "cron", "start", "--service"],
    )

    # Assert: the unit runs the scheduler, restarts it, and the OS was asked to load it.
    assert state.installed is True
    assert state.unit_path is not None
    definition = plistlib.loads(state.unit_path.read_bytes())
    assert definition["ProgramArguments"] == [
        "/usr/local/bin/opensre",
        "cron",
        "start",
        "--service",
    ]
    assert definition["KeepAlive"] is True
    assert definition["RunAtLoad"] is True
    assert [c[:2] for c in runner.commands] == [
        ["launchctl", "bootout"],
        ["launchctl", "bootstrap"],
    ]
    assert svc.background_service_state(home=tmp_path, system="Darwin").installed is True


def test_macos_remove_deletes_the_unit_after_unloading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    installed = svc.install_background_service(
        home=tmp_path, system="Darwin", run=_Runner(), command=["x"]
    )
    assert installed.unit_path is not None and installed.unit_path.exists()

    state = svc.remove_background_service(home=tmp_path, system="Darwin", run=_Runner())

    assert state.installed is False
    assert not installed.unit_path.exists()


def test_a_refused_bootstrap_raises_instead_of_reporting_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")

    with pytest.raises(RuntimeError, match="launchctl bootstrap failed: denied"):
        svc.install_background_service(
            home=tmp_path, system="Darwin", run=_Runner(failing="bootstrap"), command=["x"]
        )
    # The half-written unit is gone, so status does not claim a running service.
    assert svc.background_service_state(home=tmp_path, system="Darwin").installed is False


def test_a_service_the_os_will_not_stop_keeps_its_unit_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    installed = svc.install_background_service(
        home=tmp_path, system="Darwin", run=_Runner(), command=["x"]
    )

    with pytest.raises(RuntimeError, match="still loaded"):
        svc.remove_background_service(home=tmp_path, system="Darwin", run=_Runner(loaded=True))

    assert installed.unit_path is not None and installed.unit_path.exists()
    assert svc.background_service_state(home=tmp_path, system="Darwin").installed is True


def test_linux_install_writes_a_systemd_user_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    runner = _Runner()

    state = svc.install_background_service(
        home=tmp_path,
        system="Linux",
        run=runner,
        command=["/usr/bin/opensre", "cron", "start", "--service"],
    )

    assert state.unit_path is not None
    unit = state.unit_path.read_text()
    assert "ExecStart=/usr/bin/opensre cron start --service" in unit
    assert "Restart=always" in unit
    assert runner.commands[-1][:4] == ["systemctl", "--user", "enable", "--now"]


def test_other_platforms_are_reported_unsupported_without_touching_the_os(tmp_path: Path) -> None:
    runner = _Runner()

    state = svc.install_background_service(
        home=tmp_path, system="Windows", run=runner, command=["x"]
    )

    assert state.supported is False
    assert state.installed is False
    assert runner.commands == []
    assert "not supported on Windows" in state.summary
