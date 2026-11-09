"""The container supervisor installs the main-channel binary and restarts on update."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from config.constants.environment import CONTAINER_SUPERVISOR_PID_ENV
from config.constants.paths import REPO_ROOT
from infrastructure.deployment.container.entrypoint import (
    _INSTALL_URL,
    binary_is_current,
    mode_args,
    refresh_binary,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="The container supervisor runs under bash on Linux.",
)

_ENTRYPOINT = REPO_ROOT / "infrastructure/deployment/container/entrypoint.py"

_INSTALLER = """#!/bin/sh
cat <<'INSTALLER'
#!/bin/bash
printf '%s\\n' "$*" >> "$OPENSRE_TEST_INSTALL_LOG"
install_dir=""
previous=""
for arg in "$@"; do
  if [ "$previous" = "--install-dir" ]; then
    install_dir="$arg"
  fi
  previous="$arg"
done
mkdir -p "$install_dir"
cat > "$install_dir/opensre" << 'BINARY'
#!/bin/sh
printf '%s\\n' "$*" >> "$OPENSRE_TEST_ARGV_LOG"
if [ "$1" = "update" ]; then
  exit 0
fi
trap 'exit 0' TERM INT
while true; do
  sleep 0.05
done
BINARY
chmod 755 "$install_dir/opensre"
INSTALLER
"""


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_mode_args_match_the_container_modes() -> None:
    assert mode_args("gateway") == ("gateway", "start", "--foreground")
    assert mode_args("scheduler") == ("cron", "start", "--service")
    assert mode_args("web") == ("gateway", "web")
    with pytest.raises(SystemExit, match="unsupported MODE=worker"):
        mode_args("worker")


def test_refresh_keeps_a_current_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "opensre"
    _write_executable(binary, "#!/bin/sh\nexit 0\n")
    calls: list[list[str]] = []

    def _run(args: list[str], check: bool = False) -> subprocess.CompletedProcess[bytes]:
        del check
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(
        "infrastructure.deployment.container.entrypoint.subprocess.run",
        _run,
    )

    refresh_binary(install_dir=str(tmp_path))

    assert calls == [[str(binary), "update", "--check"]]


def test_refresh_installs_when_the_binary_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _run(args: list[str], check: bool = False) -> subprocess.CompletedProcess[bytes]:
        del check
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(
        "infrastructure.deployment.container.entrypoint.subprocess.run",
        _run,
    )

    refresh_binary(install_dir=str(tmp_path))

    assert calls[0][0] == "bash"
    assert "--main" in calls[0][2]
    assert calls[0][-2] == _INSTALL_URL
    assert calls[0][-1] == str(tmp_path)


def test_supervisor_uses_the_same_install_channel_as_opensre_update() -> None:
    update_source = (REPO_ROOT / "surfaces/cli/lifecycle/update.py").read_text(encoding="utf-8")
    entry_source = _ENTRYPOINT.read_text(encoding="utf-8")

    assert _INSTALL_URL in update_source
    assert _INSTALL_URL in entry_source
    assert CONTAINER_SUPERVISOR_PID_ENV in entry_source


def test_supervisor_restarts_the_mode_when_update_signals(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    install_dir = tmp_path / "install"
    bin_dir.mkdir()
    install_log = tmp_path / "install.log"
    argv_log = tmp_path / "argv.log"
    _write_executable(bin_dir / "curl", _INSTALLER)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["MODE"] = "gateway"
    env["OPENSRE_INSTALL_DIR"] = str(install_dir)
    env["OPENSRE_TEST_INSTALL_LOG"] = str(install_log)
    env["OPENSRE_TEST_ARGV_LOG"] = str(argv_log)
    env.pop(CONTAINER_SUPERVISOR_PID_ENV, None)

    proc = subprocess.Popen(
        [sys.executable, "-u", str(_ENTRYPOINT)],
        env=env,
    )
    try:
        _wait_until(lambda: any(line.startswith("gateway ") for line in _lines(argv_log)))
        assert any("--main" in line for line in _lines(install_log))
        proc.send_signal(signal.SIGHUP)
        _wait_until(lambda: sum(line.startswith("gateway ") for line in _lines(argv_log)) >= 2)
        assert len(_lines(install_log)) == 1
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_failed_install_with_no_binary_exits(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "curl", "#!/bin/sh\nexit 1\n")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["MODE"] = "gateway"
    env["OPENSRE_INSTALL_DIR"] = str(tmp_path / "install")
    env.pop(CONTAINER_SUPERVISOR_PID_ENV, None)

    completed = subprocess.run(
        [sys.executable, "-u", str(_ENTRYPOINT)],
        env=env,
        timeout=15,
        check=False,
    )

    assert completed.returncode != 0


def test_binary_is_current_follows_the_check_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "opensre"
    _write_executable(binary, "#!/bin/sh\nexit 0\n")

    def _run(args: list[str], check: bool = False) -> subprocess.CompletedProcess[bytes]:
        del args, check
        return subprocess.CompletedProcess([], 7)

    monkeypatch.setattr(
        "infrastructure.deployment.container.entrypoint.subprocess.run",
        _run,
    )

    assert binary_is_current(str(binary)) is False
