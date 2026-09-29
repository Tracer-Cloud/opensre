"""Native Windows compatibility tests for ``shell_run``."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psutil
import pytest

from tools.interactive_shell.shell import execution as shell_execution
from tools.interactive_shell.shell.execution import execute_shell_command

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows cmd.exe")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_WINDOWS_E2E_COMMAND = "echo native-windows-e2e&&echo escaped^&value"


def _execute(
    command: str,
    *,
    timeout_seconds: int = 8,
    cancel_event: threading.Event | None = None,
) -> shell_execution.ShellExecutionResult:
    return execute_shell_command(
        command=command,
        timeout_seconds=timeout_seconds,
        max_output_chars=10_000,
        cancel_event=cancel_event,
    )


def _output_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _python_command(script: Path, marker: Path) -> str:
    return subprocess.list2cmdline([sys.executable, str(script), str(marker)])


def _write_descendant_script(path: Path) -> None:
    path.write_text(
        """from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

marker = pathlib.Path(sys.argv[1])
pending_marker = marker.with_suffix(marker.suffix + ".pending")
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pending_marker.write_text(
    json.dumps({"parent": os.getpid(), "child": child.pid}),
    encoding="utf-8",
)
os.replace(pending_marker, marker)
time.sleep(60)
""",
        encoding="utf-8",
    )


def _read_process_ids(marker: Path) -> tuple[int, int]:
    payload = json.loads(marker.read_text(encoding="utf-8"))
    return int(payload["parent"]), int(payload["child"])


def _wait_for_pid_exit(pid: int, *, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return
        time.sleep(0.05)
    pytest.fail(f"process {pid} still alive after shell cleanup")


def _kill_pid(pid: int | None) -> None:
    if pid is None:
        return
    with contextlib.suppress(psutil.Error, OSError):
        psutil.Process(pid).kill()


def _chat_completion(message: dict[str, Any], *, finish_reason: str) -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-windows-shell-fixture",
            "object": "chat.completion",
            "created": 0,
            "model": "windows-shell-fixture",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }
    ).encode("utf-8")


def _fixture_handler(
    requests: list[dict[str, Any]],
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            content_length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(content_length))
            requests.append(request)
            messages = request.get("messages", [])
            has_tool_result = any(
                isinstance(message, dict) and message.get("role") == "tool" for message in messages
            )
            if has_tool_result:
                payload = _chat_completion(
                    {
                        "role": "assistant",
                        "content": "Native Windows shell verification completed.",
                    },
                    finish_reason="stop",
                )
            else:
                payload = _chat_completion(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_windows_shell_fixture",
                                "type": "function",
                                "function": {
                                    "name": "shell_run",
                                    "arguments": json.dumps(
                                        {
                                            "command": _WINDOWS_E2E_COMMAND,
                                            "quiet": True,
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    finish_reason="tool_calls",
                )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


@contextmanager
def _openai_fixture() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _fixture_handler(requests))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cmd_executes_compact_operators_and_preserves_streams() -> None:
    result = _execute("echo first&echo second&&echo third|findstr third")
    separated = _execute("echo stdout&(echo stderr 1>&2)")

    assert result.exit_code == 0
    assert _output_lines(result.stdout) == ["first", "second", "third"]
    assert separated.exit_code == 0
    assert _output_lines(separated.stdout) == ["stdout"]
    assert _output_lines(separated.stderr) == ["stderr"]


def test_cmd_preserves_quotes_caret_escaping_and_percent_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_WINDOWS_TEST_VALUE", "expanded")

    result = _execute(
        'echo "quoted&value"&echo escaped^&value&echo %OPENSRE_WINDOWS_TEST_VALUE%&echo 100%'
    )

    assert result.exit_code == 0
    assert _output_lines(result.stdout) == [
        '"quoted&value"',
        "escaped&value",
        "expanded",
        "100%",
    ]


def test_cmd_pwd_and_working_directory_are_isolated(tmp_path: Path) -> None:
    changed = _execute(f'cd /d "{tmp_path}"&&cd')
    unchanged = _execute("pwd")

    assert changed.exit_code == 0
    assert os.path.normcase(changed.stdout.strip()) == os.path.normcase(str(tmp_path.resolve()))
    assert unchanged.exit_code == 0
    assert os.path.normcase(unchanged.stdout.strip()) == os.path.normcase(str(Path.cwd().resolve()))


@pytest.mark.timeout(30)
def test_cmd_timeout_reaps_process_tree(tmp_path: Path) -> None:
    script = tmp_path / "spawn_descendant.py"
    marker = tmp_path / "descendant.pid"
    _write_descendant_script(script)
    parent_pid: int | None = None
    child_pid: int | None = None

    try:
        result = _execute(_python_command(script, marker), timeout_seconds=3)
        parent_pid, child_pid = _read_process_ids(marker)

        assert result.timed_out is True
        assert result.cancelled is False
        _wait_for_pid_exit(parent_pid)
        parent_pid = None
        _wait_for_pid_exit(child_pid)
        child_pid = None
    finally:
        _kill_pid(child_pid)
        _kill_pid(parent_pid)


@pytest.mark.timeout(30)
def test_cmd_cancel_reaps_process_tree(tmp_path: Path) -> None:
    script = tmp_path / "spawn_descendant.py"
    marker = tmp_path / "descendant.pid"
    _write_descendant_script(script)
    cancel_event = threading.Event()
    parent_pid: int | None = None
    child_pid: int | None = None

    def _cancel_after_descendant_starts() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.01)
        if marker.exists():
            cancel_event.set()

    threading.Thread(target=_cancel_after_descendant_starts, daemon=True).start()
    try:
        result = _execute(
            _python_command(script, marker),
            timeout_seconds=15,
            cancel_event=cancel_event,
        )
        parent_pid, child_pid = _read_process_ids(marker)

        assert result.cancelled is True
        assert result.timed_out is False
        _wait_for_pid_exit(parent_pid)
        parent_pid = None
        _wait_for_pid_exit(child_pid)
        child_pid = None
    finally:
        _kill_pid(child_pid)
        _kill_pid(parent_pid)


@pytest.mark.timeout(90)
def test_opensre_ask_executes_shell_run_through_native_cmd(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None

    with _openai_fixture() as (base_url, requests):
        env = os.environ.copy()
        env.update(
            {
                "CUSTOM_OPENAI_API_KEY": "fixture-key",
                "CUSTOM_OPENAI_BASE_URL": base_url,
                "CUSTOM_OPENAI_MODEL": "windows-shell-fixture",
                "LLM_PROVIDER": "custom-openai",
                "OPENSRE_ACCOUNT_METADATA_PATH": str(tmp_path / "account.json"),
                "OPENSRE_ACCOUNT_TOKEN": "",
                "OPENSRE_CICD": "1",
                "OPENSRE_DISABLE_KEYRING": "1",
                "OPENSRE_PROJECT_ENV_PATH": str(tmp_path / ".env"),
                "OPENSRE_REACT_GOAL_LLM_REVIEW": "0",
                "SENTRY_DSN": "",
            }
        )
        completed = subprocess.run(
            [
                uv,
                "run",
                "--project",
                str(_PROJECT_ROOT),
                "opensre",
                "--json",
                "ask",
                "--ephemeral",
                "Run the native Windows shell compatibility check.",
                "--allowed-tool",
                "shell_run",
            ],
            cwd=_PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "success"
    assert len(requests) == 2
    tool_result = next(
        message for message in requests[1]["messages"] if message.get("role") == "tool"
    )
    provider_content = json.loads(tool_result["content"])
    assert isinstance(provider_content, str)
    shell_payload = json.loads(provider_content)
    assert shell_payload["ok"] is True
    assert shell_payload["exit_code"] == 0
    assert "native-windows-e2e" in shell_payload["stdout"]
    assert "escaped&value" in shell_payload["stdout"]
