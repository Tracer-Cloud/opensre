from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import surfaces.cli.lifecycle.windows.powershell as powershell
from config.constants.installer import (
    OPENSRE_AUTO_LAUNCH_ENV,
    OPENSRE_INSTALL_CHANNEL_ENV,
    OPENSRE_INSTALL_DIR_ENV,
    OPENSRE_UPDATE_EXECUTABLE_ENV,
    OPENSRE_UPDATE_PARENT_PID_ENV,
    OPENSRE_UPDATE_PARENT_STARTED_ENV,
    OPENSRE_VERSION_ENV,
    POWERSHELL_MODULE_PATH_ENV,
)
from config.version import get_opensre_version
from infrastructure.process.release_version import (
    MAIN_BUILD_RELEASE_URL,
    fetch_latest_version,
    is_editable_install,
    is_update_available,
)
from surfaces.cli.lifecycle.windows.layout import (
    MalformedWindowsInstallError,
    classify_windows_binary_install,
)
from surfaces.cli.lifecycle.windows.processes import windows_process_identity

_INSTALL_SCRIPT = "https://install.opensre.com"
_INSTALL_SCRIPT_PS1 = "https://install.opensre.com"


def _is_binary_install() -> bool:
    return bool(getattr(sys, "frozen", False))


def _is_windows() -> bool:
    return sys.platform == "win32"


def _powershell_single_quoted_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _windows_retry_install_dir() -> Path | None:
    if not _is_binary_install():
        return None
    try:
        installation = classify_windows_binary_install(Path(sys.executable))
    except MalformedWindowsInstallError:
        return None
    if installation.app_root is not None:
        return installation.app_root.parent
    return installation.executable.parent


def _windows_retry_hint() -> str:
    commands = [
        f'$env:{POWERSHELL_MODULE_PATH_ENV}="$PSHOME\\Modules"',
        f"$env:{OPENSRE_INSTALL_CHANNEL_ENV}='main'",
        f"Remove-Item Env:{OPENSRE_VERSION_ENV} -ErrorAction SilentlyContinue",
    ]
    install_dir = _windows_retry_install_dir()
    if install_dir is not None:
        commands.append(
            f"$env:{OPENSRE_INSTALL_DIR_ENV}={_powershell_single_quoted_literal(install_dir)}"
        )
    commands.append(f"irm {_INSTALL_SCRIPT_PS1} | iex")
    return "; ".join(commands)


def _upgrade_via_install_script() -> int:
    """Download and run the official install script on the rolling main channel."""
    if _is_windows():
        env = powershell.windows_powershell_environment()
        env[OPENSRE_AUTO_LAUNCH_ENV] = "0"
        if _is_binary_install():
            identity, identity_error = windows_process_identity(
                os.getpid(), expected_executable=Path(sys.executable)
            )
            if identity_error is not None or identity is None:
                detail = identity_error or "unknown process identity error"
                print(
                    f"  error: could not verify running OpenSRE process identity: {detail}",
                    file=sys.stderr,
                )
                return 1
            env[OPENSRE_UPDATE_PARENT_PID_ENV] = str(identity.pid)
            env[OPENSRE_UPDATE_EXECUTABLE_ENV] = str(identity.executable)
            env[OPENSRE_UPDATE_PARENT_STARTED_ENV] = str(identity.started_filetime_utc)
        else:
            env.pop(OPENSRE_UPDATE_PARENT_PID_ENV, None)
            env.pop(OPENSRE_UPDATE_EXECUTABLE_ENV, None)
            env.pop(OPENSRE_UPDATE_PARENT_STARTED_ENV, None)
        install_command = powershell.windows_powershell_51_script(
            f"$env:{OPENSRE_INSTALL_CHANNEL_ENV}='main'; "
            f"Remove-Item Env:{OPENSRE_VERSION_ENV} -ErrorAction SilentlyContinue; "
            f"irm {_INSTALL_SCRIPT_PS1} | iex"
        )
        result = subprocess.run(
            [
                powershell.windows_powershell_executable(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                install_command,
            ],
            check=False,
            env=env,
        )
    else:
        result = subprocess.run(
            ["bash", "-c", f"curl -fsSL {_INSTALL_SCRIPT} | bash -s -- --main"],
            check=False,
        )
    return result.returncode


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
    else:
        print(f"  install script failed (exit {rc}).", file=sys.stderr)
        if _is_windows():
            hint = _windows_retry_hint()
            retry_context = "open a new Windows PowerShell window and run"
        else:
            hint = f"curl -fsSL {_INSTALL_SCRIPT} | bash -s -- --main"
            retry_context = "run"
        print(f"  to retry manually, {retry_context}:\n    {hint}", file=sys.stderr)
    return rc
