"""Install the current OpenSRE main build and run this container's MODE.

The image ships the toolchain (git, the GitHub CLI, Node, Codex) and this
supervisor. It does not contain an OpenSRE checkout. Each start installs the
rolling main-channel binary when the one on disk is older. ``opensre update``
in a process started here signals this supervisor, which starts that mode again
on the new binary.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence

_INSTALL_URL = "https://install.opensre.com"
_DEFAULT_INSTALL_DIR = "/home/opensre/.local/bin"
_SUPERVISOR_PID_ENV = "OPENSRE_SUPERVISOR_PID"
_POLL_SECONDS = 0.2

_MODE_ARGS = {
    "gateway": ("gateway", "start", "--foreground"),
    "scheduler": ("cron", "start", "--service"),
    "web": ("gateway", "web"),
}


def mode_args(mode: str) -> tuple[str, ...]:
    """Argv after the binary for ``MODE``. Web is the HTTP app alone."""
    try:
        return _MODE_ARGS[mode]
    except KeyError:
        raise SystemExit(f"unsupported MODE={mode}") from None


def binary_path(install_dir: str) -> str:
    """Path of the installed ``opensre`` launcher."""
    return os.path.join(install_dir, "opensre")


def _executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def install_binary(*, install_dir: str) -> None:
    """Install the rolling main-channel binary into ``install_dir``."""
    print(f"installing OpenSRE main build into {install_dir}", flush=True)
    subprocess.run(
        [
            "bash",
            "-c",
            'set -o pipefail; curl -fsSL "$1" | bash -s -- --main --install-dir "$2"',
            "opensre-container-install",
            _INSTALL_URL,
            install_dir,
        ],
        check=True,
    )


def binary_is_current(binary: str) -> bool:
    """Whether ``binary update --check`` reports this build is the main build."""
    completed = subprocess.run([binary, "update", "--check"], check=False)
    return completed.returncode == 0


def refresh_binary(*, install_dir: str) -> None:
    """Install the main build when it is missing or older than the published one.

    A refresh that fails after a binary is already installed starts that binary
    so a release-host outage does not take down a running gateway.
    """
    binary = binary_path(install_dir)
    if _executable(binary) and binary_is_current(binary):
        print("OpenSRE main build is current", flush=True)
        return
    try:
        install_binary(install_dir=install_dir)
    except subprocess.CalledProcessError:
        if _executable(binary):
            print(
                "warning: could not refresh opensre; starting the installed binary",
                file=sys.stderr,
                flush=True,
            )
            return
        raise


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return


class _Gate:
    """Stop and reload flags set from signal handlers."""

    def __init__(self) -> None:
        self.stop = False
        self.reload = False
        self.proc: subprocess.Popen[bytes] | None = None


def _handle_stop(gate: _Gate, _signum: int, _frame: object) -> None:
    gate.stop = True


def _handle_reload(gate: _Gate, _signum: int, _frame: object) -> None:
    gate.reload = True


def _child_env(base: Mapping[str, str], install_dir: str) -> dict[str, str]:
    env = dict(base)
    env["PATH"] = install_dir + os.pathsep + env.get("PATH", "")
    env["OPENSRE_AUTO_LAUNCH"] = "0"
    env["OPENSRE_SKIP_GH_INSTALL"] = "1"
    env[_SUPERVISOR_PID_ENV] = str(os.getpid())
    return env


def _command(install_dir: str, mode: str) -> tuple[str, ...]:
    return (binary_path(install_dir), *mode_args(mode))


def supervise(
    *,
    install_dir: str | None = None,
    mode: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Refresh the binary, run ``mode``, and restart it when signaled to reload."""
    env_source = os.environ if environ is None else environ
    directory = (
        install_dir
        if install_dir is not None
        else env_source.get("OPENSRE_INSTALL_DIR", _DEFAULT_INSTALL_DIR)
    )
    selected = mode if mode is not None else env_source.get("MODE", "web")
    command = _command(directory, selected)
    child_env = _child_env(env_source, directory)
    os.environ.update(child_env)

    gate = _Gate()
    signal.signal(signal.SIGTERM, lambda signum, frame: _handle_stop(gate, signum, frame))
    signal.signal(signal.SIGINT, lambda signum, frame: _handle_stop(gate, signum, frame))
    signal.signal(signal.SIGHUP, lambda signum, frame: _handle_reload(gate, signum, frame))

    while True:
        if gate.stop:
            return 0
        refresh_binary(install_dir=directory)
        if gate.stop:
            return 0
        print(f"starting opensre {' '.join(command[1:])}", flush=True)
        gate.proc = subprocess.Popen(command, env=child_env)
        while gate.proc.poll() is None:
            if gate.stop or gate.reload:
                _terminate(gate.proc)
                break
            time.sleep(_POLL_SECONDS)
        status = gate.proc.wait()
        gate.proc = None
        if gate.stop:
            return status
        if gate.reload:
            gate.reload = False
            continue
        return status


def main(argv: Sequence[str] | None = None) -> int:
    """Run the container supervisor. ``argv`` is unused; MODE comes from the environment."""
    del argv
    try:
        return supervise()
    except subprocess.CalledProcessError as exc:
        print(f"opensre install failed (exit {exc.returncode})", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
