"""The request a real Codex binary sends to the hosted route carries only function tools.

The hosted OpenAI-compatible route rejects any request whose ``tools`` holds a
non-``function`` entry ("Only local function tools are allowed."), which failed
every gateway repair run. Codex 0.156.1 adds a hosted ``web_search`` tool and a
``multi_agent_v1`` ``namespace`` tool by default, so this drives the installed
binary against a loopback stand-in for the route and checks the body it posts.
Skipped when no Codex binary is installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from integrations.llm_cli.codex import hosted_provider_overrides

_CODEX_TIMEOUT_SECONDS = 90.0
_ROUTE_REJECTION = json.dumps(
    {"error": {"message": "stand-in route stops here", "type": "invalid_request_error"}}
).encode()


def _codex_binary() -> str | None:
    return os.environ.get("CODEX_BIN") or shutil.which("codex")


def _recording_handler(bodies: list[dict[str, Any]]) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length") or 0)
            if self.path.endswith("/responses"):
                bodies.append(json.loads(self.rfile.read(length)))
            self.send_response(HTTPStatus.BAD_REQUEST)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(_ROUTE_REJECTION)))
            self.end_headers()
            self.wfile.write(_ROUTE_REJECTION)

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NOT_FOUND)
            self.send_header("content-length", "0")
            self.end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
            return None

    return _Handler


@pytest.fixture
def stand_in_route() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    bodies: list[dict[str, Any]] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _recording_handler(bodies))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", bodies
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


@pytest.mark.skipif(_codex_binary() is None, reason="Codex CLI not installed")
def test_hosted_codex_posts_only_function_tools(
    stand_in_route: tuple[str, list[dict[str, Any]]], tmp_path: Path
) -> None:
    # Arrange: an empty Codex home, like the gateway's, pointed at the stand-in route
    base_url, bodies = stand_in_route
    binary = _codex_binary()
    assert binary is not None
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    argv = [
        binary,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "-s",
        "danger-full-access",
        "--color",
        "never",
        "-C",
        str(tmp_path),
        *hosted_provider_overrides(base_url),
        "-c",
        "model_providers.opensre.request_max_retries=0",
        "-c",
        "model_providers.opensre.stream_max_retries=0",
        "-m",
        "gpt-6.1-sol",
        "say hi",
    ]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "CODEX_HOME": str(codex_home),
        "OPENAI_API_KEY": "stand-in-token",
        "NO_COLOR": "1",
    }

    # Act
    subprocess.run(
        argv,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_CODEX_TIMEOUT_SECONDS,
        check=False,
    )

    # Assert: Codex reached the route, and every tool it offered is a function tool
    assert bodies, "Codex never posted to /responses"
    tool_types = {(tool.get("type"), tool.get("name")) for tool in bodies[0].get("tools", [])}
    assert tool_types, "Codex sent no tools; the shape check would pass vacuously"
    assert {kind for kind, _ in tool_types} == {"function"}, tool_types
