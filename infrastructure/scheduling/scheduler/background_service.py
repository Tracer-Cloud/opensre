"""Keep the scheduler running when no shell is open: a per-user OS service.

macOS gets a launchd LaunchAgent, Linux a systemd user unit; both run
``opensre cron start --service`` and restart it when it exits. Other
platforms are reported as unsupported rather than guessed at. The unit
records the build it was installed from, so a service still running an older
build can be told apart and restarted.
"""

from __future__ import annotations

import logging
import os
import platform
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from config.constants.paths import OPENSRE_HOME_DIR, OPENSRE_HOME_ENV
from config.constants.scheduler import OPENSRE_SCHEDULER_BUILD_ENV
from config.version import get_build_stamp
from infrastructure.process.entrypoint import opensre_command
from infrastructure.scheduling.scheduler.operation_log import record_scheduler_service_operation
from infrastructure.scheduling.scheduler.storage import has_live_claim

logger = logging.getLogger(__name__)

SERVICE_LABEL = "com.opensre.scheduler"
_LOGS_DIRNAME = "logs"
_COMMAND_TIMEOUT_SECONDS = 30
_UNLOAD_WAIT_SECONDS = 15.0
_UNLOAD_POLL_SECONDS = 0.5
_SYSTEMD_BUILD_LINE = re.compile(
    rf'^Environment="?{OPENSRE_SCHEDULER_BUILD_ENV}=(?P<build>[^"\n]*)"?$', re.MULTILINE
)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class BackgroundServiceState:
    """What the OS knows about the scheduler service."""

    platform: str
    supported: bool
    installed: bool
    unit_path: Path | None
    log_path: Path | None
    detail: str = ""
    running: bool = False

    @property
    def summary(self) -> str:
        if not self.supported:
            return f"Background scheduling is not supported on {self.platform}; {self.detail}"
        if not self.installed:
            return "No background scheduler service is installed."
        return f"Background scheduler service installed: {self.unit_path}"


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT_SECONDS,
        check=False,
    )


def scheduler_command() -> list[str]:
    """The command the service runs; the installed ``opensre`` when present."""
    executable = shutil.which("opensre")
    if executable:
        return [executable, "cron", "start", "--service"]
    return [sys.executable, "-m", "surfaces.entrypoint", "cron", "start", "--service"]


def install_background_service(
    *,
    home: Path | None = None,
    system: str = "",
    run: Runner = _run,
    command: Sequence[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    build: str = "",
) -> BackgroundServiceState:
    """Install and (re)start the service; raises ``RuntimeError`` when the OS refuses.

    A running service is replaced, so it runs the unit just written. A refused
    activation removes the unit again, so status never reports a service the
    OS is not running.
    """
    name = system or platform.system()
    argv = list(command or scheduler_command())
    stamp = build or get_build_stamp()
    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if name == "Darwin":
        unit = _launchd_unit_path(home)
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_bytes(plistlib.dumps(_launchd_definition(argv, log_path, stamp)))
        domain = f"gui/{os.getuid()}"
        target = f"{domain}/{SERVICE_LABEL}"
        run(["launchctl", "bootout", target])
        # bootout only starts the teardown; bootstrapping the same label while
        # the old process is still exiting fails with "5: Input/output error".
        if not _wait_until_unloaded(run, ["launchctl", "print", target], sleep=sleep):
            unit.unlink(missing_ok=True)
            raise RuntimeError(
                "launchctl bootstrap skipped: the previous scheduler service is still "
                "stopping; try again in a few seconds"
            )
        _activate(unit, run(["launchctl", "bootstrap", domain, str(unit)]), "launchctl bootstrap")
        return BackgroundServiceState("Darwin", True, True, unit, log_path)
    if name == "Linux":
        unit = _systemd_unit_path(home)
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_systemd_definition(argv, log_path, stamp), encoding="utf-8")
        service = f"{SERVICE_LABEL}.service"
        _activate(unit, run(["systemctl", "--user", "daemon-reload"]), "systemctl daemon-reload")
        _activate(unit, run(["systemctl", "--user", "enable", service]), "systemctl enable")
        # ``enable --now`` would leave an already running service on its old
        # process; restart starts a stopped one and replaces a running one.
        _activate(unit, run(["systemctl", "--user", "restart", service]), "systemctl restart")
        return BackgroundServiceState("Linux", True, True, unit, log_path)
    return _unsupported(name)


def remove_background_service(
    *,
    home: Path | None = None,
    system: str = "",
    run: Runner = _run,
    sleep: Callable[[float], None] = time.sleep,
) -> BackgroundServiceState:
    """Stop and delete the service; a missing service is not an error.

    The unit is deleted only once the OS confirms the service is no longer
    loaded, so a refused stop raises ``RuntimeError`` and leaves the unit for
    a retry.
    """
    name = system or platform.system()
    if name == "Darwin":
        unit = _launchd_unit_path(home)
        target = f"gui/{os.getuid()}/{SERVICE_LABEL}"
        run(["launchctl", "bootout", target])
        if not _wait_until_unloaded(run, ["launchctl", "print", target], sleep=sleep):
            raise RuntimeError("launchctl bootout failed: the service is still loaded")
        unit.unlink(missing_ok=True)
        return BackgroundServiceState("Darwin", True, False, None, None)
    if name == "Linux":
        unit = _systemd_unit_path(home)
        service = f"{SERVICE_LABEL}.service"
        run(["systemctl", "--user", "disable", "--now", service])
        if run(["systemctl", "--user", "is-active", service]).returncode == 0:
            raise RuntimeError("systemctl disable failed: the service is still active")
        unit.unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"])
        return BackgroundServiceState("Linux", True, False, None, None)
    return _unsupported(name)


def background_service_state(
    *, home: Path | None = None, system: str = ""
) -> BackgroundServiceState:
    """Whether the service unit exists on this machine."""
    name = system or platform.system()
    if name == "Darwin":
        unit = _launchd_unit_path(home)
    elif name == "Linux":
        unit = _systemd_unit_path(home)
    else:
        return _unsupported(name)
    installed = unit.exists()
    return BackgroundServiceState(
        name, True, installed, unit if installed else None, _log_path() if installed else None
    )


def _unsupported(name: str) -> BackgroundServiceState:
    return BackgroundServiceState(
        name,
        False,
        False,
        None,
        None,
        detail="run `opensre cron start --service` from your own scheduler instead.",
    )


def check_background_service(
    *,
    home: Path | None = None,
    system: str = "",
    run: Runner = _run,
) -> BackgroundServiceState:
    """Check OS liveness rather than interpreting a saved unit as a running daemon."""
    state = background_service_state(home=home, system=system)
    if not state.supported or not state.installed:
        return state
    if state.platform == "Darwin":
        result = run(["launchctl", "print", f"gui/{os.getuid()}/{SERVICE_LABEL}"])
        alive = result.returncode == 0 and "state = running" in result.stdout
    else:
        result = run(["systemctl", "--user", "is-active", f"{SERVICE_LABEL}.service"])
        alive = result.returncode == 0 and result.stdout.strip() == "active"
    return BackgroundServiceState(
        state.platform,
        state.supported,
        state.installed,
        state.unit_path,
        state.log_path,
        running=alive,
    )


def ensure_background_service(
    *,
    home: Path | None = None,
    system: str = "",
    run: Runner = _run,
    deadline: float | None = None,
) -> BackgroundServiceState:
    """Start this installation's scheduler and require an OS-confirmed live process.

    A running service whose unit records another build is restarted so new
    ticks run this code. While any scheduled execution holds a live lease that
    restart is skipped: stopping the service would kill the execution, and a
    repair worker's supervisor would mark it failed. A later call restarts it.
    """
    original_run = run

    def bounded_run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        remaining = deadline - time.time() if deadline is not None else _COMMAND_TIMEOUT_SECONDS
        if remaining <= 0:
            raise RuntimeError("The background scheduler setup deadline expired.")
        if original_run is _run:
            return subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=min(_COMMAND_TIMEOUT_SECONDS, remaining),
                check=False,
            )
        return original_run(command)

    run = bounded_run
    state = check_background_service(home=home, system=system, run=run)
    if not state.supported:
        raise RuntimeError(state.summary)
    command = opensre_command("cron", "start", "--service")
    build = get_build_stamp()
    same_installation, installed_build = _installed_definition(state, command)
    stale_build = same_installation and installed_build != build
    if state.running and stale_build:
        details = {"installed_build": installed_build or "unrecorded", "build": build}
        if _scheduled_execution_in_flight():
            logger.info(
                "Scheduler service restart for build %s deferred: a run is in flight", build
            )
            record_scheduler_service_operation("scheduler_service_restart_deferred", extra=details)
            return state
        logger.info("Restarting the scheduler service on build %s", build)
        record_scheduler_service_operation("scheduler_service_build_restart", extra=details)
    if not state.running or not same_installation or stale_build:
        install_background_service(home=home, system=system, run=run, command=command, build=build)
    for _attempt in range(10):
        state = check_background_service(home=home, system=system, run=run)
        if state.running:
            return state
        time.sleep(0.5)
    raise RuntimeError("The background scheduler did not become healthy; check its service log.")


def restart_stale_background_service(
    *,
    home: Path | None = None,
    system: str = "",
    run: Runner = _run,
) -> bool:
    """Restart a running service left on another build, once no run is in flight.

    ``ensure_background_service`` defers that restart while an execution holds
    a lease and only runs again when a repair is scheduled, so the shell calls
    this at startup to finish a deferred upgrade. True when it restarted.
    """
    state = check_background_service(home=home, system=system, run=run)
    if not state.supported or not state.running:
        return False
    command = opensre_command("cron", "start", "--service")
    build = get_build_stamp()
    same_installation, installed_build = _installed_definition(state, command)
    if not same_installation or installed_build == build or _scheduled_execution_in_flight():
        return False
    logger.info("Restarting the scheduler service on build %s", build)
    record_scheduler_service_operation(
        "scheduler_service_build_restart",
        extra={"installed_build": installed_build or "unrecorded", "build": build},
    )
    install_background_service(home=home, system=system, run=run, command=command, build=build)
    return True


def _installed_definition(
    state: BackgroundServiceState, command: Sequence[str]
) -> tuple[bool, str]:
    """Whether the unit runs ``command`` for this OpenSRE home, and the build it records.

    A unit written before builds were recorded reports ``""`` as its build.
    """
    if state.unit_path is None:
        return False, ""
    if state.platform == "Darwin":
        definition = plistlib.loads(state.unit_path.read_bytes())
        environment = definition.get("EnvironmentVariables", {})
        same = definition.get("ProgramArguments") == list(command) and environment.get(
            OPENSRE_HOME_ENV
        ) == str(OPENSRE_HOME_DIR)
        return same, str(environment.get(OPENSRE_SCHEDULER_BUILD_ENV) or "")
    unit = state.unit_path.read_text()
    match = _SYSTEMD_BUILD_LINE.search(unit)
    build = match.group("build") if match else ""
    return unit == _systemd_definition(command, _log_path(), build), build


def _scheduled_execution_in_flight() -> bool:
    """Whether a scheduled execution holds a live lease; unreadable run state counts as one."""
    try:
        return has_live_claim()
    except (OSError, sqlite3.Error):
        logger.warning("Could not read scheduler run state; keeping the running service.")
        return True


def _wait_until_unloaded(
    run: Runner, query: Sequence[str], *, sleep: Callable[[float], None]
) -> bool:
    """Poll ``query`` (a service status command) until it reports no service, or give up."""
    deadline = time.monotonic() + _UNLOAD_WAIT_SECONDS
    while run(query).returncode == 0:
        if time.monotonic() >= deadline:
            return False
        sleep(_UNLOAD_POLL_SECONDS)
    return True


def _activate(unit: Path, result: subprocess.CompletedProcess[str], step: str) -> None:
    """Fail an install step, removing the unit so a half-installed service is not reported."""
    if result.returncode != 0:
        unit.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{step} failed: {detail or f'exit code {result.returncode}'}")


def _log_path() -> Path:
    return OPENSRE_HOME_DIR / _LOGS_DIRNAME / "scheduler.log"


def _launchd_unit_path(home: Path | None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"


def _systemd_unit_path(home: Path | None) -> Path:
    return (home or Path.home()) / ".config" / "systemd" / "user" / f"{SERVICE_LABEL}.service"


def _launchd_definition(argv: Sequence[str], log_path: Path, build: str) -> dict[str, object]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        OPENSRE_HOME_ENV: str(OPENSRE_HOME_DIR),
    }
    if build:
        environment[OPENSRE_SCHEDULER_BUILD_ENV] = build
    return {
        "Label": SERVICE_LABEL,
        "ProgramArguments": list(argv),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "EnvironmentVariables": environment,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }


def _systemd_definition(argv: Sequence[str], log_path: Path, build: str) -> str:
    """The unit text; an empty ``build`` reproduces a unit written before builds were recorded."""
    exec_start = " ".join(_systemd_quote(part) for part in argv)
    build_line = (
        f"Environment={_systemd_quote(f'{OPENSRE_SCHEDULER_BUILD_ENV}={build}')}\n" if build else ""
    )
    return (
        "[Unit]\n"
        "Description=OpenSRE scheduler\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={exec_start}\n"
        f"Environment={_systemd_quote(f'{OPENSRE_HOME_ENV}={OPENSRE_HOME_DIR}')}\n"
        f"{build_line}"
        "Restart=always\n"
        "RestartSec=10\n"
        f"StandardOutput=append:{log_path}\n"
        f"StandardError=append:{log_path}\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemd_quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


__all__ = [
    "SERVICE_LABEL",
    "BackgroundServiceState",
    "background_service_state",
    "check_background_service",
    "ensure_background_service",
    "install_background_service",
    "remove_background_service",
    "restart_stale_background_service",
    "scheduler_command",
]
