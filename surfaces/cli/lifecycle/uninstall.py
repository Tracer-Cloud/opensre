from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from config.constants.installer import (
    WINDOWS_APP_DIR_NAME,
    WINDOWS_INSTALL_LOCK_FILENAME,
    WINDOWS_LAUNCHER_FILENAME,
)
from config.constants.paths import OPENSRE_HOME_DIR
from surfaces.cli.lifecycle.windows import (
    MalformedWindowsInstallError,
    WindowsBinaryInstall,
    classify_windows_binary_install,
    schedule_windows_cleanup,
    schedule_windows_managed_cleanup,
    windows_processes_using_tree,
)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_binary_install() -> bool:
    return bool(getattr(sys, "frozen", False))


def _remove_path(p: Path) -> tuple[bool, str | None]:
    if not p.exists() and not p.is_symlink():
        return True, None
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return True, None
    except OSError as exc:
        return False, str(exc)


def _pip_uninstall() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "--yes", "opensre"],
        check=False,
        capture_output=True,
    )
    return result.returncode


def _data_dirs() -> list[Path]:
    return [
        OPENSRE_HOME_DIR,
        Path.home() / ".config" / "opensre",
    ]


def _is_onedir_binary(exe_path: Path) -> bool:
    return exe_path.parent.name == ".opensre-app" and (exe_path.parent / "_internal").is_dir()


def _launcher_for_binary(exe_path: Path) -> Path | None:
    launcher = shutil.which("opensre")
    if not launcher:
        return None
    launcher_path = Path(launcher)
    try:
        if launcher_path.resolve() == exe_path.resolve():
            return launcher_path
    except OSError:
        return None
    return None


def _binary_install_paths(exe_path: Path | None = None) -> list[Path]:
    exe = exe_path or Path(sys.executable)
    paths: list[Path] = []
    if launcher := _launcher_for_binary(exe):
        paths.append(launcher)
    if _is_onedir_binary(exe):
        paths.append(exe.parent)
    else:
        paths.append(exe)

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def run_uninstall(*, yes: bool = False) -> int:
    dirs = _data_dirs()
    binary = _is_binary_install()
    windows_binary = binary and _is_windows()
    windows_install: WindowsBinaryInstall | None = None
    if windows_binary:
        try:
            windows_install = classify_windows_binary_install()
        except MalformedWindowsInstallError as exc:
            print(f"  error    {exc}", file=sys.stderr)
            print("           Nothing was deleted.", file=sys.stderr)
            print(
                "           Inspect or move the unverified .opensre-app directory aside, "
                "then reinstall with install.ps1 before retrying uninstall.",
                file=sys.stderr,
            )
            return 1
        binary_paths = list(windows_install.paths)
    else:
        binary_paths = _binary_install_paths() if binary else []

    print()
    print("  The following will be permanently deleted:")
    print()
    for d in dirs:
        tag = "found" if d.exists() else "not found"
        print(f"    {d}  ({tag})")
    if binary:
        for path in binary_paths:
            print(f"    {path}  (binary)")
    else:
        print("    pip package: opensre")
    print()

    if not yes:
        try:
            import questionary

            confirmed = questionary.confirm(
                "  Uninstall opensre from this machine?", default=False
            ).ask()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return 1
        if not confirmed:
            print("  Cancelled.")
            return 0

    if windows_binary:
        # Deliberate revalidation, not a redundant call: the layout can change while
        # the confirmation prompt is open, so ownership is re-proven before any
        # deletion is scheduled.
        try:
            windows_install = classify_windows_binary_install()
        except MalformedWindowsInstallError as exc:
            print(f"  error    {exc}", file=sys.stderr)
            print("           Nothing was deleted.", file=sys.stderr)
            return 1
        binary_paths = list(windows_install.paths)
        if windows_install.app_root is not None:
            running, process_error = windows_processes_using_tree(
                windows_install.app_root,
                current_pid=os.getpid(),
            )
            if process_error is not None:
                print(f"  error    {process_error}", file=sys.stderr)
                print(
                    "           Nothing was deleted. Close other OpenSRE processes and retry.",
                    file=sys.stderr,
                )
                return 1
            if running:
                print(
                    "  error    another OpenSRE process is using this Windows bundle:",
                    file=sys.stderr,
                )
                for pid, process_path in running:
                    print(f"           PID {pid}: {process_path}", file=sys.stderr)
                print(
                    "           Nothing was deleted. Close the other OpenSRE process and retry.",
                    file=sys.stderr,
                )
                return 1

    print()

    any_error = False
    deferred_cleanup = False
    if windows_binary:
        assert windows_install is not None
        if windows_install.app_root is not None:
            ok, err = schedule_windows_managed_cleanup(
                executable=windows_install.executable,
                app_root=windows_install.app_root,
                launcher=windows_install.launcher,
                parent_pid=os.getpid(),
                data_paths=dirs,
            )
        else:
            install_dir = windows_install.executable.parent
            ok, err = schedule_windows_cleanup(
                binary_paths,
                parent_pid=os.getpid(),
                data_paths=dirs,
                install_lock_path=install_dir / WINDOWS_INSTALL_LOCK_FILENAME,
                data_guard_paths=[
                    install_dir / WINDOWS_APP_DIR_NAME,
                    install_dir / WINDOWS_LAUNCHER_FILENAME,
                    install_dir / windows_install.executable.name,
                ],
            )
        if not ok:
            print(f"  error    could not schedule binary cleanup: {err}", file=sys.stderr)
            print("           Nothing was deleted.", file=sys.stderr)
            return 1
        deferred_cleanup = True
        for path in binary_paths:
            print(f"  scheduled {path}  (after this process exits)")
        for path in dirs:
            print(f"  scheduled {path}  (after binary cleanup succeeds)")

    if not windows_binary:
        for d in dirs:
            if not d.exists():
                print(f"  skipped  {d}  (not found)")
                continue
            ok, err = _remove_path(d)
            if ok:
                print(f"  deleted  {d}")
            else:
                print(f"  error    {d}: {err}", file=sys.stderr)
                any_error = True

    if binary:
        if not windows_binary:
            for path in binary_paths:
                ok, err = _remove_path(path)
                if ok:
                    print(f"  deleted  {path}")
                else:
                    print(f"  error    {path}: {err}", file=sys.stderr)
                    any_error = True
    else:
        print("  running  pip uninstall opensre")
        rc = _pip_uninstall()
        if rc == 0:
            print("  deleted  pip package opensre")
        else:
            print(f"  error    pip uninstall failed (exit {rc})", file=sys.stderr)
            if _is_windows():
                hint = "pip uninstall opensre"
            else:
                hint = "pip uninstall opensre  (or: pipx uninstall opensre)"
            print(f"           retry manually: {hint}", file=sys.stderr)
            any_error = True

    print()

    if any_error:
        print("  Uninstall finished with errors. See above for details.", file=sys.stderr)
        return 1

    if deferred_cleanup:
        print("  opensre will finish uninstalling after this process exits.")
        print()
        print("  Your config and data will be removed after binary cleanup succeeds.")
    else:
        print("  opensre has been uninstalled.")
        print()
        print("  Your config and data have been removed.")
    if _is_windows():
        print("  To reinstall: irm https://install.opensre.com | iex")
    else:
        print("  To reinstall: curl -fsSL https://install.opensre.com | bash")
    return 0
