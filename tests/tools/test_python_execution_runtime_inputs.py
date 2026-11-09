"""Tests for runtime facts injected into the Python execution sandbox."""

from __future__ import annotations

import importlib.metadata
import json
import os

from config.runtime_metadata import RUNTIME_INPUTS_KEY
from config.version import get_opensre_version
from tools.system.python_execution_tool import execute_python_code


def test_reports_version_via_injected_runtime_inputs() -> None:
    result = execute_python_code.run(
        code="print(inputs['opensre_runtime']['opensre_version'])",
    )
    assert result["success"] is True
    assert get_opensre_version() in result["stdout"]
    assert RUNTIME_INPUTS_KEY in result["inputs"]


def test_reports_current_time_via_injected_runtime_inputs() -> None:
    """Sandbox path should surface a fresh ``now_iso`` (not a bootstrap
    snapshot) so scripts asking for the current time never see a stale value."""
    result = execute_python_code.run(
        code="print(inputs['opensre_runtime']['now_iso'])",
    )
    assert result["success"] is True
    stdout = result["stdout"].strip()
    assert stdout, "now_iso should be non-empty"
    assert "T" in stdout, f"expected ISO 8601 datetime, got {stdout!r}"


def test_reports_process_and_python_facts_via_injected_runtime_inputs() -> None:
    """The no-subprocess replacement path: a script asking for python version,
    PID, parent PID, uptime, kubeconfig, or the installed tools list should
    read them from ``inputs['opensre_runtime']``."""
    result = execute_python_code.run(
        code=(
            "import json\n"
            "runtime = inputs['opensre_runtime']\n"
            "print(json.dumps({\n"
            "    'py': runtime['python_version'],\n"
            "    'pid': runtime['pid'],\n"
            "    'ppid': runtime['ppid'],\n"
            "    'uptime': runtime['uptime_seconds'],\n"
            "    'kubeconfig': runtime['kubeconfig'],\n"
            "    'tools': sorted(k for k, v in runtime['tools'].items() if v),\n"
            "}))\n"
        ),
    )
    assert result["success"] is True, result
    payload = json.loads(result["stdout"].strip())
    assert payload["pid"] == os.getpid()
    assert payload["py"].count(".") == 2
    assert isinstance(payload["uptime"], (int, float))
    assert payload["uptime"] >= 0.0


def test_filesystem_introspection_without_subprocess() -> None:
    """Sandbox filesystem introspection: scratchpad listing via pathlib,
    hostname and disk/memory from injected facts — no subprocess."""
    result = execute_python_code.run(
        code=(
            "import json\n"
            "from pathlib import Path\n"
            "runtime = inputs['opensre_runtime']\n"
            "scratch = Path(runtime['scratchpad_dir'])\n"
            "entries = sorted(p.name for p in scratch.iterdir())[:3]\n"
            "print(json.dumps({\n"
            "    'hostname': runtime['hostname'],\n"
            "    'scratchpad': str(scratch),\n"
            "    'listable': isinstance(entries, list),\n"
            "    'disk_used': runtime.get('disk_used_percent'),\n"
            "    'memory_used': runtime.get('memory_used_percent'),\n"
            "}))\n"
        ),
    )
    assert result["success"] is True, result
    payload = json.loads(result["stdout"].strip())
    assert payload["hostname"]
    assert payload["listable"] is True
    assert isinstance(payload["disk_used"], (int, float))
    assert isinstance(payload["memory_used"], (int, float))


def test_socket_reachability_check_works_with_allow_network() -> None:
    """The no-curl/ping replacement path: reachability via
    socket.create_connection inside the sandbox when the caller opts into
    network access. A local listener keeps the test deterministic."""
    import socket
    import threading

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    accepted = threading.Thread(target=lambda: listener.accept(), daemon=True)
    accepted.start()
    try:
        result = execute_python_code.run(
            code=(
                "import socket\n"
                f"conn = socket.create_connection(('127.0.0.1', {port}), timeout=3)\n"
                "print('reachable')\n"
                "conn.close()\n"
            ),
            allow_network=True,
        )
    finally:
        listener.close()
    assert result["success"] is True, result
    assert "reachable" in result["stdout"]


def test_socket_reachability_blocked_without_allow_network() -> None:
    """Default sandbox posture stays closed: no network without the opt-in."""
    result = execute_python_code.run(
        code=("import socket\nsocket.create_connection(('127.0.0.1', 9), timeout=1)\n"),
    )
    assert result["success"] is False
    assert "PermissionError" in (result["stderr"] + result["stdout"])


def test_reports_version_via_importlib_metadata() -> None:
    """Sandbox can read package metadata; that is not always get_opensre_version().

    Dev checkouts keep ``pyproject`` / installed metadata at the base (``0.1``)
    while ``get_opensre_version()`` expands it to ``0.1+branch.sha``. A
    release build embeds the full string in metadata, so both agree. This test
    only asserts the sandbox sees the same installed metadata as the host.
    """
    result = execute_python_code.run(
        code=("import importlib.metadata as m\nprint(m.version('opensre'))\n"),
    )
    assert result["success"] is True
    assert result["stdout"].strip() == importlib.metadata.version("opensre")


def test_still_blocks_subprocess_version_check() -> None:
    result = execute_python_code.run(
        code="import subprocess; subprocess.run(['opensre', '--version'])",
    )
    assert result["success"] is False
    assert "PermissionError" in result["stderr"] or "PermissionError" in result["stdout"]
