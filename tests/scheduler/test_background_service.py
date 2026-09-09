"""Tests for the per-user background scheduler service."""

from __future__ import annotations

import plistlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler import background_service as svc


class _Runner:
    """Records OS commands; ``failing`` names a command word that exits 1, ``loaded_for``
    is how many status checks still report the service as loaded (``-1`` = forever)."""

    def __init__(self, failing: str = "", loaded_for: int = 0) -> None:
        self.commands: list[list[str]] = []
        self._failing = failing
        self._loaded_for = loaded_for

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        self.commands.append(argv)
        if argv[:2] == ["launchctl", "print"] or argv[2:3] == ["is-active"]:
            loaded = self._loaded_for != 0
            if self._loaded_for > 0:
                self._loaded_for -= 1
            return subprocess.CompletedProcess(argv, 0 if loaded else 3, "", "")
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
        ["launchctl", "print"],
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
        svc.remove_background_service(
            home=tmp_path, system="Darwin", run=_Runner(loaded_for=-1), sleep=lambda _s: None
        )

    assert installed.unit_path is not None and installed.unit_path.exists()
    assert svc.background_service_state(home=tmp_path, system="Darwin").installed is True


def test_reinstall_waits_for_the_old_service_to_unload_before_bootstrapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: the previous service is still tearing down for two status checks.
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    runner = _Runner(loaded_for=2)
    naps: list[float] = []

    # Act
    state = svc.install_background_service(
        home=tmp_path, system="Darwin", run=runner, command=["x"], sleep=naps.append
    )

    # Assert: bootstrap ran only after the status check reported the service gone.
    assert state.installed is True
    names = [c[:2] for c in runner.commands]
    assert names.index(["launchctl", "bootstrap"]) > names.index(["launchctl", "print"])
    assert len(naps) == 2


def test_reinstall_gives_up_when_the_old_service_never_unloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    monkeypatch.setattr(svc, "_UNLOAD_WAIT_SECONDS", 0.0)

    with pytest.raises(RuntimeError, match="still stopping"):
        svc.install_background_service(
            home=tmp_path,
            system="Darwin",
            run=_Runner(loaded_for=-1),
            command=["x"],
            sleep=lambda _s: None,
        )
    assert svc.background_service_state(home=tmp_path, system="Darwin").installed is False


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
