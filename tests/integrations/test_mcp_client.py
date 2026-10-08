"""Regression tests for the shared MCP transport helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import patch

import httpx
import mcp_types as types
import pytest

import integrations.mcp_client as mcp_client
from integrations.mcp_transport import McpTransportMode
from integrations.posthog_mcp import (
    PostHogMCPConfig,
    call_posthog_mcp_tool,
    list_posthog_mcp_tools,
)
from integrations.sentry_mcp import SentryMCPConfig, call_sentry_mcp_tool, list_sentry_mcp_tools
from integrations.x_mcp import XMCPConfig, call_x_mcp_tool, list_x_mcp_tools


@dataclass(frozen=True)
class _Config:
    mode: McpTransportMode = McpTransportMode.STREAMABLE_HTTP
    url: str = "https://mcp.example.test/mcp"
    command: str = ""
    args: tuple[str, ...] = ()
    timeout_seconds: float = 5.0

    @property
    def request_headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer token"}


@dataclass(frozen=True)
class _ListToolsResult:
    tools: list[types.Tool]
    next_cursor: str | None = None


class _Session:
    async def list_tools(
        self, *, params: types.PaginatedRequestParams | None = None
    ) -> _ListToolsResult:
        assert params is None
        return _ListToolsResult(tools=[types.Tool(name="status", input_schema={})])

    async def call_tool(self, name: str, arguments: dict[str, object]) -> types.CallToolResult:
        assert name == "status"
        assert arguments == {"verbose": True}
        return types.CallToolResult(
            content=[
                types.TextContent(text="ready"),
                types.EmbeddedResource(
                    resource=types.TextResourceContents(
                        uri="file:///runbook.md",
                        text="runbook",
                    )
                ),
            ],
            structured_content={"ok": True},
        )


@asynccontextmanager
async def _open_session(*_args: object, **_kwargs: object) -> AsyncIterator[_Session]:
    yield _Session()


def _session_options() -> mcp_client.McpSessionOptions:
    return {
        "session_url": "https://mcp.example.test/mcp",
        "stdio_env": {},
        "integration_name": "Example",
        "config_env_name": "EXAMPLE_MCP",
    }


def test_shared_client_normalizes_list_and_tool_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_client, "open_mcp_session", _open_session)

    tools = mcp_client.list_mcp_tools(_Config(), **_session_options())
    result = mcp_client.call_mcp_tool(
        _Config(),
        "status",
        {"verbose": True},
        timeout_call=True,
        **_session_options(),
    )

    assert [tool.name for tool in tools] == ["status"]
    assert result == {
        "is_error": False,
        "text": "ready\nrunbook",
        "content": [
            {"type": "text", "text": "ready"},
            {"type": "resource_text", "uri": "file:///runbook.md", "text": "runbook"},
        ],
        "structured_content": {"ok": True},
        "tool": "status",
        "arguments": {"verbose": True},
    }


def test_tool_call_timeout_is_marked_as_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowSession:
        async def call_tool(
            self, _name: str, _arguments: dict[str, object]
        ) -> types.CallToolResult:
            await asyncio.sleep(1)
            raise AssertionError("unreachable")

    @asynccontextmanager
    async def open_session(*_args: object, **_kwargs: object) -> AsyncIterator[SlowSession]:
        yield SlowSession()

    monkeypatch.setattr(mcp_client, "open_mcp_session", open_session)

    with pytest.raises(mcp_client.McpToolCallOutcomeUnknownError) as error:
        mcp_client.call_mcp_tool(
            _Config(timeout_seconds=0.01),
            "restart_service",
            timeout_call=False,
            timeout_entire_operation=True,
            **_session_options(),
        )

    assert isinstance(error.value.__cause__, TimeoutError)


def test_shared_client_collects_all_tool_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_cursors: list[str | None] = []

    class PaginatedSession(_Session):
        async def list_tools(
            self, *, params: types.PaginatedRequestParams | None = None
        ) -> _ListToolsResult:
            cursor = params.cursor if params is not None else None
            requested_cursors.append(cursor)
            if cursor is None:
                return _ListToolsResult(
                    tools=[types.Tool(name="first", input_schema={})],
                    next_cursor="page-2",
                )
            assert cursor == "page-2"
            return _ListToolsResult(tools=[types.Tool(name="second", input_schema={})])

    @asynccontextmanager
    async def open_session(*_args: object, **_kwargs: object) -> AsyncIterator[PaginatedSession]:
        yield PaginatedSession()

    monkeypatch.setattr(mcp_client, "open_mcp_session", open_session)

    tools = mcp_client.list_mcp_tools(_Config(), **_session_options())

    assert [tool.name for tool in tools] == ["first", "second"]
    assert requested_cursors == [None, "page-2"]


def test_shared_client_rejects_repeated_tool_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    class RepeatingSession(_Session):
        async def list_tools(
            self, *, params: types.PaginatedRequestParams | None = None
        ) -> _ListToolsResult:
            del params
            return _ListToolsResult(tools=[], next_cursor="same-cursor")

    @asynccontextmanager
    async def open_session(*_args: object, **_kwargs: object) -> AsyncIterator[RepeatingSession]:
        yield RepeatingSession()

    monkeypatch.setattr(mcp_client, "open_mcp_session", open_session)

    with pytest.raises(RuntimeError, match="repeated pagination cursor"):
        mcp_client.list_mcp_tools(_Config(), **_session_options())


def test_shared_client_bounds_accumulated_tool_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OversizedSession(_Session):
        async def list_tools(
            self, *, params: types.PaginatedRequestParams | None = None
        ) -> _ListToolsResult:
            del params
            return _ListToolsResult(
                tools=[
                    types.Tool(
                        name="large",
                        description="x" * 200,
                        input_schema={},
                    )
                ],
                next_cursor="another-page",
            )

    @asynccontextmanager
    async def open_session(*_args: object, **_kwargs: object) -> AsyncIterator[OversizedSession]:
        yield OversizedSession()

    monkeypatch.setattr(mcp_client, "open_mcp_session", open_session)
    monkeypatch.setattr(mcp_client, "MCP_TOOL_LIST_MAX_SERIALIZED_CHARS", 100)

    with pytest.raises(RuntimeError, match="serialized-size safety limit"):
        mcp_client.list_mcp_tools(_Config(), **_session_options())


def test_shared_client_bounds_one_descriptor_without_serializing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OversizedSession(_Session):
        async def list_tools(
            self, *, params: types.PaginatedRequestParams | None = None
        ) -> _ListToolsResult:
            del params
            return _ListToolsResult(
                tools=[types.Tool(name="large", description="x" * 200, input_schema={})]
            )

    @asynccontextmanager
    async def open_session(*_args: object, **_kwargs: object) -> AsyncIterator[OversizedSession]:
        yield OversizedSession()

    monkeypatch.setattr(mcp_client, "open_mcp_session", open_session)
    monkeypatch.setattr(mcp_client, "MCP_TOOL_LIST_MAX_SERIALIZED_CHARS", 100)

    with (
        patch.object(types.Tool, "model_dump_json", side_effect=AssertionError),
        pytest.raises(RuntimeError, match="serialized-size safety limit"),
    ):
        mcp_client.list_mcp_tools(_Config(), **_session_options())


class _ChunkedResponseStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.chunks_read = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_tool_discovery_stops_reading_a_chunked_oversized_response(method: str) -> None:
    stream = _ChunkedResponseStream([b"1234", b"56", b"unread"])
    response = httpx.Response(
        200,
        request=httpx.Request(method, "https://mcp.example.test/mcp"),
        stream=stream,
    )

    async def consume() -> None:
        await mcp_client._response_size_hook(5)(response)
        await response.aread()

    with pytest.raises(mcp_client.McpResponseTooLargeError, match="byte limit"):
        asyncio.run(consume())

    assert stream.chunks_read == 2
    assert stream.closed is True


def test_tool_discovery_rejects_declared_oversized_response_before_reading() -> None:
    stream = _ChunkedResponseStream([b"unread"])
    response = httpx.Response(
        200,
        headers={"Content-Length": "6"},
        request=httpx.Request("POST", "https://mcp.example.test/mcp"),
        stream=stream,
    )

    with pytest.raises(mcp_client.McpResponseTooLargeError, match="byte limit"):
        asyncio.run(mcp_client._response_size_hook(5)(response))

    assert stream.chunks_read == 0
    assert stream.closed is True


def test_tool_discovery_rejects_compressed_responses_before_decoding() -> None:
    stream = _ChunkedResponseStream([b"unread"])
    response = httpx.Response(
        200,
        headers={"Content-Encoding": "gzip"},
        request=httpx.Request("POST", "https://mcp.example.test/mcp"),
        stream=stream,
    )

    with pytest.raises(mcp_client.McpResponseTooLargeError, match="compression is disabled"):
        asyncio.run(mcp_client._response_size_hook(5)(response))

    assert stream.chunks_read == 0
    assert stream.closed is True


def test_tool_discovery_opens_session_with_wire_response_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def open_session(*_args: object, **kwargs: object) -> AsyncIterator[_Session]:
        captured.update(kwargs)
        yield _Session()

    monkeypatch.setattr(mcp_client, "open_mcp_session", open_session)

    mcp_client.list_mcp_tools(_Config(), **_session_options())

    assert captured["response_byte_limit"] == mcp_client.MCP_TOOL_LIST_MAX_RESPONSE_BYTES


def test_shared_client_keeps_vendor_timeout_copy_for_chained_timeout() -> None:
    outer = RuntimeError("request failed")
    outer.__cause__ = TimeoutError()

    assert (
        mcp_client.root_cause_message(outer, timeout_message="Example MCP tool call timed out")
        == "Example MCP tool call timed out"
    )


class _InitializedSession:
    def __init__(self, read_stream: object, write_stream: object) -> None:
        self.streams = (read_stream, write_stream)
        self.initialized = False

    async def __aenter__(self) -> _InitializedSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def initialize(self) -> None:
        self.initialized = True


def test_open_session_characterizes_stdio_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp.client import session as session_module
    from mcp.client import stdio as stdio_module

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def open_stdio(parameters: object) -> AsyncIterator[tuple[str, str]]:
        captured["parameters"] = parameters
        yield ("read", "write")

    sessions: list[_InitializedSession] = []

    def build_session(read_stream: object, write_stream: object) -> _InitializedSession:
        session = _InitializedSession(read_stream, write_stream)
        sessions.append(session)
        return session

    monkeypatch.setattr(stdio_module, "stdio_client", open_stdio)
    monkeypatch.setattr(session_module, "ClientSession", build_session)
    config = _Config(mode=McpTransportMode.STDIO, command="npx", args=("server",))

    async def open_session() -> None:
        async with mcp_client.open_mcp_session(
            config,
            **{**_session_options(), "stdio_env": {"TOKEN": "secret"}},
        ):
            return None

    asyncio.run(open_session())

    parameters = cast(Any, captured["parameters"])
    assert parameters.command == "npx"
    assert parameters.args == ["server"]
    assert parameters.env["TOKEN"] == "secret"
    assert parameters.env["NO_COLOR"] == "1"
    assert parameters.env["TERM"] == "dumb"
    assert sessions[0].streams == ("read", "write")
    assert sessions[0].initialized is True


def test_open_session_characterizes_sse_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp.client import session as session_module
    from mcp.client import sse as sse_module

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def open_sse(*args: object, **kwargs: object) -> AsyncIterator[tuple[str, str]]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        yield ("read", "write")

    monkeypatch.setattr(sse_module, "sse_client", open_sse)
    monkeypatch.setattr(
        session_module,
        "ClientSession",
        _InitializedSession,
    )
    config = _Config(mode=McpTransportMode.SSE, timeout_seconds=7.0)

    asyncio.run(_consume_session(config))

    assert captured == {
        "args": ("https://mcp.example.test/mcp",),
        "kwargs": {
            "headers": {"Authorization": "Bearer token"},
            "timeout": 7.0,
            "sse_read_timeout": 60.0,
        },
    }


def test_open_session_characterizes_streamable_http_wiring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def open_http_client(**kwargs: object) -> AsyncIterator[str]:
        captured["http_client"] = kwargs
        yield "client"

    @asynccontextmanager
    async def open_streamable(
        *args: object, **kwargs: object
    ) -> AsyncIterator[tuple[str, str, None]]:
        captured["streamable"] = {"args": args, "kwargs": kwargs}
        yield ("read", "write", None)

    from mcp.client import session as session_module

    monkeypatch.setattr(mcp_client.httpx, "AsyncClient", open_http_client)
    monkeypatch.setattr(mcp_client, "streamable_http_client", open_streamable)
    monkeypatch.setattr(
        session_module,
        "ClientSession",
        _InitializedSession,
    )
    config = _Config(timeout_seconds=7.0)

    asyncio.run(_consume_session(config, response_byte_limit=123))

    http_client = captured["http_client"]
    assert isinstance(http_client, dict)
    assert http_client["headers"] == {
        "Authorization": "Bearer token",
        "Accept-Encoding": "identity",
    }
    hooks = cast(dict[str, list[object]], http_client["event_hooks"])
    assert len(hooks["response"]) == 1
    assert callable(hooks["response"][0])
    timeout = http_client["timeout"]
    assert isinstance(timeout, mcp_client.httpx.Timeout)
    assert timeout.connect == 7.0
    assert timeout.read == 60.0
    assert captured["streamable"] == {
        "args": ("https://mcp.example.test/mcp",),
        "kwargs": {
            "http_client": "client",
            "headers": {
                "Authorization": "Bearer token",
                "Accept-Encoding": "identity",
            },
            "timeout": 7.0,
            "sse_read_timeout": 60.0,
        },
    }


async def _consume_session(config: _Config, **kwargs: object) -> None:
    async with mcp_client.open_mcp_session(config, **_session_options(), **kwargs):
        return None


@pytest.mark.parametrize(
    ("config", "list_tools", "expected_options"),
    [
        (
            PostHogMCPConfig(
                url="https://posthog.example.test/mcp",
                auth_token="posthog-token",
                features=("flags",),
            ),
            list_posthog_mcp_tools,
            {
                "session_url": "https://posthog.example.test/mcp?features=flags",
                "stdio_env": {
                    "POSTHOG_AUTH_HEADER": "Bearer posthog-token",
                    "POSTHOG_PERSONAL_API_KEY": "posthog-token",
                },
                "integration_name": "PostHog",
                "config_env_name": "POSTHOG_MCP",
                "sse_url_hint": "https://mcp.posthog.com/sse",
            },
        ),
        (
            SentryMCPConfig(
                url="https://sentry.example.test/mcp",
                auth_token="sentry-token",
                host="sentry.example.test",
                skills=("inspect",),
            ),
            list_sentry_mcp_tools,
            {
                "session_url": "https://sentry.example.test/mcp",
                "stdio_env": {
                    "SENTRY_ACCESS_TOKEN": "sentry-token",
                    "SENTRY_HOST": "sentry.example.test",
                    "MCP_SKILLS": "inspect",
                },
                "integration_name": "Sentry",
                "config_env_name": "SENTRY_MCP",
                "sse_url_hint": "https://mcp.sentry.dev/sse",
            },
        ),
        (
            XMCPConfig(
                url="http://127.0.0.1:9000/mcp",
                auth_token="proxy-token",
                bearer_token="x-token",
            ),
            list_x_mcp_tools,
            {
                "session_url": "http://127.0.0.1:9000/mcp",
                "stdio_env": {"X_BEARER_TOKEN": "x-token"},
                "integration_name": "X",
                "config_env_name": "X_MCP",
                "sse_url_hint": "http://127.0.0.1:8000/sse",
                "streamable_url_hint": "http://127.0.0.1:8000/mcp",
                "timeout_entire_operation": True,
            },
        ),
    ],
)
def test_vendor_list_operations_preserve_connection_options(
    monkeypatch: pytest.MonkeyPatch,
    config: Any,
    list_tools: Any,
    expected_options: dict[str, object],
) -> None:
    captured: dict[str, object] = {}

    def record_list_tools(received_config: object, **kwargs: object) -> list[types.Tool]:
        captured["config"] = received_config
        captured["options"] = kwargs
        return []

    module_name = list_tools.__module__
    module = __import__(module_name, fromlist=["list_mcp_tools"])
    monkeypatch.setattr(module, "list_mcp_tools", record_list_tools)

    assert list_tools(config) == []
    assert captured == {"config": config, "options": expected_options}


@pytest.mark.parametrize(
    ("config", "call_tool", "expected_timeout_options"),
    [
        (
            PostHogMCPConfig(auth_token="posthog-token"),
            call_posthog_mcp_tool,
            {"timeout_call": True},
        ),
        (
            SentryMCPConfig(auth_token="sentry-token"),
            call_sentry_mcp_tool,
            {"timeout_call": True},
        ),
        (
            XMCPConfig(),
            call_x_mcp_tool,
            {"timeout_call": False, "timeout_entire_operation": True},
        ),
    ],
)
def test_vendor_calls_preserve_timeout_scope(
    monkeypatch: pytest.MonkeyPatch,
    config: Any,
    call_tool: Any,
    expected_timeout_options: dict[str, bool],
) -> None:
    captured: dict[str, object] = {}

    def record_call(
        received_config: object,
        tool_name: str,
        arguments: dict[str, object] | None,
        **kwargs: object,
    ) -> dict[str, object]:
        captured.update(
            config=received_config,
            tool_name=tool_name,
            arguments=arguments,
            options=kwargs,
        )
        return {}

    module_name = call_tool.__module__
    module = __import__(module_name, fromlist=["call_mcp_tool"])
    monkeypatch.setattr(module, "call_mcp_tool", record_call)

    assert call_tool(config, "status", {"verbose": True}) == {}
    assert captured["config"] is config
    assert captured["tool_name"] == "status"
    assert captured["arguments"] == {"verbose": True}
    options = cast(dict[str, object], captured["options"])
    assert {key: options[key] for key in expected_timeout_options} == expected_timeout_options
