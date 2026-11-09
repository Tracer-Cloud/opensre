"""Tests for the per-user background scheduler service."""

from __future__ import annotations

import plistlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from config.constants.scheduler import OPENSRE_SCHEDULER_BUILD_ENV
from infrastructure.scheduling.scheduler import background_service as svc

_BUILD = "0.1.2026.10.3+main.98e2e6e"
_OLDER_BUILD = "0.1.2026.9.16+main.64d2aad"


@pytest.fixture(autouse=True)
def _current_build_and_idle_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the running build and keep the run-state read off the developer's database."""
    monkeypatch.setattr(svc, "get_build_stamp", lambda: _BUILD)
    monkeypatch.setattr(svc, "has_live_claim", lambda: False)


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
    assert definition["EnvironmentVariables"][OPENSRE_SCHEDULER_BUILD_ENV] == _BUILD
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
    assert f"Environment={OPENSRE_SCHEDULER_BUILD_ENV}={_BUILD}\n" in unit
    assert "Restart=always" in unit
    # ``enable --now`` would keep an already running service on its old process.
    assert [c[2] for c in runner.commands] == ["daemon-reload", "enable", "restart"]


def test_other_platforms_are_reported_unsupported_without_touching_the_os(tmp_path: Path) -> None:
    runner = _Runner()

    state = svc.install_background_service(
        home=tmp_path, system="Windows", run=runner, command=["x"]
    )

    assert state.supported is False
    assert state.installed is False
    assert runner.commands == []
    assert "not supported on Windows" in state.summary


def test_installed_but_crashed_service_is_not_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    installed = svc.install_background_service(
        home=tmp_path, system="Darwin", run=_Runner(), command=["x"]
    )
    assert installed.installed

    def crashed(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            list(command), 0, "state = waiting\nlast exit code = 1", ""
        )

    state = svc.check_background_service(home=tmp_path, system="Darwin", run=crashed)
    assert state.installed and not state.running


def test_expired_setup_deadline_prevents_service_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    runner = _Runner()
    with pytest.raises(RuntimeError, match="deadline expired"):
        svc.ensure_background_service(home=tmp_path, system="Darwin", run=runner, deadline=0)
    assert not runner.commands


class _LiveService:
    """launchd/systemd stand-in: the service runs until booted out and comes back when started."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self._loaded = True

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        self.commands.append(argv)
        verb = argv[1] if argv[0] == "launchctl" else argv[2]
        if verb == "bootout":
            self._loaded = False
        elif verb == "bootstrap":
            self._loaded = True
        if verb == "print":
            stdout = "state = running\n" if self._loaded else ""
            return subprocess.CompletedProcess(argv, 0 if self._loaded else 113, stdout, "")
        if verb == "is-active":
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    @property
    def restarts(self) -> int:
        return sum(
            1 for argv in self.commands if argv[1:2] == ["bootstrap"] or argv[2:3] == ["restart"]
        )


def _write_unstamped_launch_agent(home: Path) -> Path:
    """This installation's launch agent as written before units recorded their build."""
    unit = home / "Library" / "LaunchAgents" / f"{svc.SERVICE_LABEL}.plist"
    unit.parent.mkdir(parents=True)
    definition = {
        "Label": svc.SERVICE_LABEL,
        "ProgramArguments": svc.opensre_command("cron", "start", "--service"),
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin",
            "OPENSRE_HOME": str(svc.OPENSRE_HOME_DIR),
        },
    }
    unit.write_bytes(plistlib.dumps(definition))
    return unit


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_a_running_service_on_an_older_build_is_restarted_once(
    system: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: this installation's service is running, installed by an older build.
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    command = svc.opensre_command("cron", "start", "--service")
    svc.install_background_service(
        home=tmp_path, system=system, run=_LiveService(), command=command, build=_OLDER_BUILD
    )
    service = _LiveService()

    # Act
    first = svc.ensure_background_service(home=tmp_path, system=system, run=service)
    after_restart = service.restarts
    svc.ensure_background_service(home=tmp_path, system=system, run=service)

    # Assert: one restart onto this build; the next check leaves the service alone.
    assert first.running is True
    assert after_restart == 1
    assert service.restarts == 1
    assert first.unit_path is not None
    if system == "Darwin":
        environment = plistlib.loads(first.unit_path.read_bytes())["EnvironmentVariables"]
        assert environment[OPENSRE_SCHEDULER_BUILD_ENV] == _BUILD
    else:
        assert f"{OPENSRE_SCHEDULER_BUILD_ENV}={_BUILD}" in first.unit_path.read_text()


def test_a_build_restart_waits_until_no_scheduled_run_is_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: a pre-stamp service is mid-way through a scheduled run (a repair worker).
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    unit = _write_unstamped_launch_agent(tmp_path)
    in_flight = {"value": True}
    monkeypatch.setattr(svc, "has_live_claim", lambda: in_flight["value"])
    service = _LiveService()

    # Act
    deferred = svc.ensure_background_service(home=tmp_path, system="Darwin", run=service)

    # Assert: the old service keeps running its execution; nothing was stopped.
    assert deferred.running is True
    assert not [argv for argv in service.commands if argv[1] in {"bootout", "bootstrap"}]
    environment = plistlib.loads(unit.read_bytes())["EnvironmentVariables"]
    assert OPENSRE_SCHEDULER_BUILD_ENV not in environment

    # Act: a later check, once the run finished, restarts onto this build.
    in_flight["value"] = False
    svc.ensure_background_service(home=tmp_path, system="Darwin", run=service)

    assert service.restarts == 1
    environment = plistlib.loads(unit.read_bytes())["EnvironmentVariables"]
    assert environment[OPENSRE_SCHEDULER_BUILD_ENV] == _BUILD


def test_shell_startup_finishes_a_deferred_build_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: a pre-stamp service whose restart was deferred by an in-flight run.
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    unit = _write_unstamped_launch_agent(tmp_path)
    in_flight = {"value": True}
    monkeypatch.setattr(svc, "has_live_claim", lambda: in_flight["value"])
    service = _LiveService()

    # Act / Assert: still busy, so nothing restarts; once idle, one restart.
    assert not svc.restart_stale_background_service(home=tmp_path, system="Darwin", run=service)
    in_flight["value"] = False
    assert svc.restart_stale_background_service(home=tmp_path, system="Darwin", run=service)
    assert not svc.restart_stale_background_service(home=tmp_path, system="Darwin", run=service)

    assert service.restarts == 1
    environment = plistlib.loads(unit.read_bytes())["EnvironmentVariables"]
    assert environment[OPENSRE_SCHEDULER_BUILD_ENV] == _BUILD


def test_shell_startup_leaves_a_stopped_service_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "OPENSRE_HOME_DIR", tmp_path / ".opensre")
    _write_unstamped_launch_agent(tmp_path)
    monkeypatch.setattr(svc, "has_live_claim", lambda: False)
    monkeypatch.setattr(
        svc,
        "check_background_service",
        lambda **_kw: svc.BackgroundServiceState("Darwin", True, True, None, None, running=False),
    )

    assert not svc.restart_stale_background_service(home=tmp_path, system="Darwin")
