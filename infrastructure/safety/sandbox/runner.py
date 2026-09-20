"""Python sandbox runner with timeout and restricted access."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from config.constants import OPENSRE_TMP_DIR, ensure_opensre_tmp_dir

DEFAULT_TIMEOUT: int = 30
MAX_TIMEOUT: int = 60
_SANDBOX_TMP_ROOT = os.path.realpath(os.fspath(OPENSRE_TMP_DIR))
_BASE_ENV_KEYS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMPDIR",
    "TMP",
    "USERPROFILE",
    "WINDIR",
)
_PYTHON_EXECUTABLE_NAMES = ("python3", "python")
_PYTHON_PROBE = "import sys; raise SystemExit(sys.version_info[0] != 3)"
_PYTHON_PROBE_TIMEOUT = 3

# Preamble injected before user code when network access is disabled.
_NETWORK_BLOCK_PREAMBLE = textwrap.dedent("""\
    import socket as _socket_module

    class _BlockedSocket:
        def __init__(self, *args, **kwargs):
            raise PermissionError("Network access is not permitted in sandbox mode")

    _socket_module.socket = _BlockedSocket

    def _blocked_create_connection(*args, **kwargs):
        raise PermissionError("Network access is not permitted in sandbox mode")

    def _blocked_getaddrinfo(*args, **kwargs):
        raise PermissionError("Network access is not permitted in sandbox mode")

    _socket_module.create_connection = _blocked_create_connection
    _socket_module.getaddrinfo = _blocked_getaddrinfo
""")

# Preamble always injected before user code: restricts filesystem writes and subprocesses.
_SANDBOX_PREAMBLE = textwrap.dedent(f"""\
    import builtins as _builtins_module
    import os as _os_module

    _ALLOWED_WRITE_ROOTS = ({_SANDBOX_TMP_ROOT!r},)

    _original_open = _builtins_module.open

    def _restricted_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes)) or hasattr(file, "__fspath__"):
            mode_str = str(mode)
            if any(c in mode_str for c in ("w", "a", "x")):
                file_str = _os_module.fsdecode(file)
                abs_path = _os_module.path.realpath(_os_module.fspath(file_str))
                norm_path = _os_module.path.normcase(abs_path)
                if not any(
                    norm_path == _os_module.path.normcase(root) or norm_path.startswith(_os_module.path.normcase(root) + _os_module.sep)
                    for root in _ALLOWED_WRITE_ROOTS
                ):
                    raise PermissionError(
                        f"Write access denied outside the OpenSRE temp directory: {{file}}"
                    )
        return _original_open(file, mode, *args, **kwargs)

    _builtins_module.open = _restricted_open

    import subprocess as _subprocess_module
    import os as _os_shell_module

    def _blocked_subprocess(*args, **kwargs):
        raise PermissionError("Subprocess execution is not permitted in sandbox mode")

    _subprocess_module.Popen = _blocked_subprocess
    _subprocess_module.call = _blocked_subprocess
    _subprocess_module.check_call = _blocked_subprocess
    _subprocess_module.check_output = _blocked_subprocess
    _subprocess_module.run = _blocked_subprocess

    _os_shell_module.system = _blocked_subprocess
    _os_shell_module.popen = _blocked_subprocess

""")


@dataclass
class SandboxResult:
    """Result of a sandboxed Python execution."""

    code: str
    inputs: dict[str, Any]
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@lru_cache(maxsize=8)
def _validated_frozen_python(frozen_executable: str, path: str) -> str | None:
    """Resolve and validate an external Python for a frozen OpenSRE process."""
    for name in _PYTHON_EXECUTABLE_NAMES:
        for candidate in _explicit_path_candidates(name, path):
            try:
                is_frozen_executable = os.path.samefile(candidate, frozen_executable)
            except OSError:
                is_frozen_executable = os.path.normcase(
                    os.path.realpath(candidate)
                ) == os.path.normcase(os.path.realpath(frozen_executable))
            if is_frozen_executable:
                continue
            try:
                probe = subprocess.run(
                    [candidate, "-I", "-c", _PYTHON_PROBE],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=_PYTHON_PROBE_TIMEOUT,
                    env=_sandbox_env({"PATH": path}),
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if probe.returncode == 0:
                return candidate
    return None


def _explicit_path_candidates(name: str, path: str) -> Iterator[str]:
    """Yield executables found only inside absolute, explicit PATH entries."""
    seen_directories: set[str] = set()
    for entry in path.split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        directory = os.path.abspath(entry)
        normalized_directory = os.path.normcase(directory)
        if normalized_directory in seen_directories:
            continue
        seen_directories.add(normalized_directory)

        candidate = shutil.which(os.path.join(directory, name))
        if candidate is None:
            continue
        candidate = os.path.abspath(candidate)
        if os.path.normcase(os.path.dirname(candidate)) != normalized_directory:
            continue
        yield candidate


def _python_executable() -> str:
    """Return a real Python interpreter, never the frozen OpenSRE executable."""
    if not sys.executable:
        raise FileNotFoundError("Python 3 is not available")
    if not getattr(sys, "frozen", False):
        return sys.executable

    candidate = _validated_frozen_python(
        os.path.abspath(sys.executable),
        os.environ.get("PATH", ""),
    )
    if candidate is not None:
        return candidate

    raise FileNotFoundError("Python 3 is not available on PATH")


def python_interpreter_available() -> bool:
    """Return whether sandbox execution can resolve a Python interpreter."""
    try:
        _python_executable()
    except OSError:
        return False
    return True


def run_python_sandbox(
    code: str,
    inputs: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
    allow_network: bool = False,
) -> SandboxResult:
    """Run Python code in a sandboxed subprocess with timeout and access restrictions.

    Network access is blocked by replacing ``socket.socket`` and related helpers
    with a class that raises ``PermissionError``. Filesystem writes are restricted
    to the OpenSRE temp directory, so any attempt to open a file outside that
    directory for writing raises ``PermissionError``. Execution is capped at
    *timeout* seconds.

    Args:
        code: Python source code to execute.
        inputs: Optional mapping injected into the script's global scope as the
            ``inputs`` variable.
        timeout: Maximum wall-clock time in seconds.  Capped at
            :data:`MAX_TIMEOUT`.
        env: Optional approved environment variables to expose to the subprocess.
        allow_network: If True, do not inject the network-blocking preamble.

    Returns:
        :class:`SandboxResult` carrying captured stdout/stderr, exit code, and
        timeout/error metadata.
    """
    effective_timeout = min(max(1, timeout), MAX_TIMEOUT)
    effective_inputs: dict[str, Any] = inputs or {}

    inputs_injection = ""
    if effective_inputs:
        inputs_json = json.dumps(effective_inputs)
        inputs_injection = (
            f"import json as _json_module; inputs = _json_module.loads({inputs_json!r})\n"
        )

    network_preamble = "" if allow_network else _NETWORK_BLOCK_PREAMBLE
    full_code = network_preamble + _SANDBOX_PREAMBLE + inputs_injection + code

    tmp_path: str | None = None
    try:
        python_executable = _python_executable()
        ensure_opensre_tmp_dir()
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            dir=OPENSRE_TMP_DIR,
        ) as tmp:
            tmp.write(full_code)
            tmp_path = tmp.name

        result = subprocess.run(
            [python_executable, "-I", tmp_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=effective_timeout,
            env=_sandbox_env(env),
        )
        return SandboxResult(
            code=code,
            inputs=effective_inputs,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            code=code,
            inputs=effective_inputs,
            stdout="",
            stderr="",
            exit_code=-1,
            timed_out=True,
            error=f"Execution timed out after {effective_timeout} seconds",
        )
    except Exception as exc:
        return SandboxResult(
            code=code,
            inputs=effective_inputs,
            stdout="",
            stderr="",
            exit_code=-1,
            timed_out=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def _sandbox_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    """Build a narrow subprocess environment plus explicitly approved values."""
    sandbox_env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            sandbox_env[key] = value
    if extra_env:
        sandbox_env.update({key: str(value) for key, value in extra_env.items() if value})
    return sandbox_env
