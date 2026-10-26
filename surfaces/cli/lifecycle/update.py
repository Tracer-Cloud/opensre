from __future__ import annotations

import os
import signal
import subprocess
import sys

from config.constants.environment import CONTAINER_SUPERVISOR_PID_ENV
from config.version import get_opensre_version
from infrastructure.process.release_version import (
    MAIN_BUILD_RELEASE_URL,
    fetch_latest_version,
    is_editable_install,
    is_update_available,
)

_INSTALL_SCRIPT = "https://install.opensre.com"
_INSTALL_SCRIPT_PS1 = "https://install.opensre.com"


def _is_binary_install() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_windows() -> bool:
    return sys.platform == "win32"


def _upgrade_via_install_script() -> int:
    """Download and run the official install script on the rolling main channel."""
    if _is_windows():
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$env:OPENSRE_INSTALL_CHANNEL='main'; "
                    f"Remove-Item Env:OPENSRE_VERSION -ErrorAction SilentlyContinue; "
                    f"irm {_INSTALL_SCRIPT_PS1} | iex"
                ),
            ],
            check=False,
        )
    else:
        result = subprocess.run(
            ["bash", "-c", f"curl -fsSL {_INSTALL_SCRIPT} | bash -s -- --main"],
            check=False,
        )
    return result.returncode


def _container_supervisor_pid() -> int | None:
    """Pid to signal after an update, when this process was started by the container supervisor."""
    raw = os.environ.get(CONTAINER_SUPERVISOR_PID_ENV, "").strip()
    if not raw.isdigit():
        return None
    pid = int(raw)
    if pid <= 0 or pid == os.getpid():
        return None
    return pid


def _restart_container_supervisor(pid: int) -> None:
    """Ask the container supervisor to start the mode again on the new binary."""
    sighup = getattr(signal, "SIGHUP", None)
    if sighup is None:
        return
    try:
        os.kill(pid, sighup)
    except OSError as exc:
        print(
            f"  warning: could not restart the container process ({type(exc).__name__}).",
            file=sys.stderr,
        )


def run_update(*, check_only: bool = False, yes: bool = False) -> int:
    # To skip this check in CI or automated environments, set OPENSRE_NO_UPDATE_CHECK=1.
    current = get_opensre_version()

    try:
        latest = fetch_latest_version()
    except Exception as exc:
        print(f"  error: could not fetch latest version: {exc}", file=sys.stderr)
        return 1

    if not latest:
        print(
            "  error: could not determine latest main build version from release data.",
            file=sys.stderr,
        )
        return 1

    if not is_update_available(current, latest):
        print(f"  opensre {current} is already up to date.")
        return 0

    print(f"  current: {current}")
    print(f"  latest:  {latest}")
    print("  main build: " + MAIN_BUILD_RELEASE_URL)

    if check_only:
        return 1

    if is_editable_install():
        print(
            "  warning: this is an editable install — upgrading will replace it with a main build."
        )

    supervisor = _container_supervisor_pid()
    if supervisor is not None:
        yes = True

    if not yes:
        try:
            import questionary

            confirmed = questionary.confirm(f"  Update to main build {latest}?", default=True).ask()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return 1
        if not confirmed:
            print("  Cancelled.")
            return 0

    rc = _upgrade_via_install_script()
    if rc == 0:
        print(f"  updated: {current} -> {latest}")
        print("  main build release: " + MAIN_BUILD_RELEASE_URL)
        if supervisor is not None:
            _restart_container_supervisor(supervisor)
    else:
        print(f"  install script failed (exit {rc}).", file=sys.stderr)
        if _is_windows():
            hint = f"$env:OPENSRE_INSTALL_CHANNEL='main'; irm {_INSTALL_SCRIPT_PS1} | iex"
        else:
            hint = f"curl -fsSL {_INSTALL_SCRIPT} | bash -s -- --main"
        print(f"  to retry manually, run:\n    {hint}", file=sys.stderr)
    return rc
