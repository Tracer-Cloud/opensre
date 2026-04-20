"""Python sandbox runner with timeout and restricted access."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TIMEOUT: int = 30
MAX_TIMEOUT: int = 60

# Preamble injected before user code: blocks network and restricts filesystem writes.
_SANDBOX_PREAMBLE = textwrap.dedent("""\
    import socket as _socket_module
    import builtins as _builtins_module
    import os as _os_module
    import tempfile as _tempfile_module

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

    _ALLOWED_WRITE_ROOTS = (
        _os_module.path.realpath(_tempfile_module.gettempdir()),
        _os_module.path.realpath(_os_module.sep + "tmp"),
    )

    _original_open = _builtins_module.open

    def _restricted_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes)) or hasattr(file, "__fspath__"):
            mode_str = str(mode)
            if any(c in mode_str for c in ("w", "a", "x")):
                abs_path = _os_module.path.realpath(_os_module.fspath(file))
                if not any(abs_path.startswith(root) for root in _ALLOWED_WRITE_ROOTS):
                    raise PermissionError(
                        f"Write access denied outside temp directories: {file}"
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


def run_python_sandbox(
    code: str,
    inputs: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> SandboxResult:
    """Run Python code in a sandboxed subprocess with timeout and access restrictions.

    Network access is blocked by replacing ``socket.socket`` and related helpers
    with a class that raises ``PermissionError``.  Filesystem writes are restricted
    to paths under ``/tmp``; any attempt to open a file outside that directory for
    writing raises ``PermissionError``.  Execution is capped at *timeout* seconds.

    Args:
        code: Python source code to execute.
        inputs: Optional mapping injected into the script's global scope as the
            ``inputs`` variable.
        timeout: Maximum wall-clock time in seconds.  Capped at
            :data:`MAX_TIMEOUT`.

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

    full_code = _SANDBOX_PREAMBLE + inputs_injection + code

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
            tmp.write(full_code)
            tmp_path = tmp.name

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=effective_timeout,
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
