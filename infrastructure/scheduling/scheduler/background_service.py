"""Keep the scheduler running when no shell is open: a per-user OS service.

macOS gets a launchd LaunchAgent, Linux a systemd user unit; both run
``opensre cron start --service`` and restart it when it exits. Other
platforms are reported as unsupported rather than guessed at.
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from config.constants.paths import OPENSRE_HOME_DIR

SERVICE_LABEL = "com.opensre.scheduler"
_LOGS_DIRNAME = "logs"
_COMMAND_TIMEOUT_SECONDS = 30

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
) -> BackgroundServiceState:
    """Install and start the service; raises ``RuntimeError`` when the OS refuses.

    A refused activation removes the unit again, so status never reports a
    service the OS is not running.
    """
    name = system or platform.system()
    argv = list(command or scheduler_command())
    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if name == "Darwin":
        unit = _launchd_unit_path(home)
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_bytes(plistlib.dumps(_launchd_definition(argv, log_path)))
        domain = f"gui/{os.getuid()}"
        run(["launchctl", "bootout", f"{domain}/{SERVICE_LABEL}"])
        _activate(unit, run(["launchctl", "bootstrap", domain, str(unit)]), "launchctl bootstrap")
        return BackgroundServiceState("Darwin", True, True, unit, log_path)
    if name == "Linux":
        unit = _systemd_unit_path(home)
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_systemd_definition(argv, log_path), encoding="utf-8")
        _activate(unit, run(["systemctl", "--user", "daemon-reload"]), "systemctl daemon-reload")
        _activate(
            unit,
            run(["systemctl", "--user", "enable", "--now", f"{SERVICE_LABEL}.service"]),
            "systemctl enable",
        )
        return BackgroundServiceState("Linux", True, True, unit, log_path)
    return _unsupported(name)


def remove_background_service(
    *, home: Path | None = None, system: str = "", run: Runner = _run
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
        if run(["launchctl", "print", target]).returncode == 0:
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


def _launchd_definition(argv: Sequence[str], log_path: Path) -> dict[str, object]:
    return {
        "Label": SERVICE_LABEL,
        "ProgramArguments": list(argv),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }


def _systemd_definition(argv: Sequence[str], log_path: Path) -> str:
    exec_start = " ".join(_systemd_quote(part) for part in argv)
    return (
        "[Unit]\n"
        "Description=OpenSRE scheduler\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={exec_start}\n"
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
    "install_background_service",
    "remove_background_service",
    "scheduler_command",
]
