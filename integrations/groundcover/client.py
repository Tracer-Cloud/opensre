"""groundcover MCP client.

The only module that knows groundcover MCP wire details. groundcover is reached
through its public streamable-HTTP MCP endpoint (JSON-RPC, optionally SSE-framed).
This client owns transport, auth/routing headers, timeouts, bounded retries,
secret redaction, and error normalization. Tool modules call the typed methods
here and never build JSON-RPC payloads directly.

v1 is read-only: every method maps to a public, read-only groundcover MCP tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import httpx
import mcp_types as types

from infrastructure.observability.errors.service import capture_service_error
from integrations.groundcover.config import GroundcoverIntegrationConfig
from integrations.mcp_streamable_http_compat import streamable_http_client
from integrations.probes import ProbeResult

if TYPE_CHECKING:
    from mcp.client.session import ClientSession  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

GroundcoverConfig = GroundcoverIntegrationConfig

# Short connect/init timeout (seconds); SSE reads can stream longer than a single request.
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_SSE_READ_TIMEOUT_SECONDS = 120.0
# One retry only, and only for connection-level failures. MCP query tools are
# read-only/idempotent, but we keep the retry budget tiny to fail fast.
_MAX_CONNECT_RETRIES = 1

# Tools that must exist for verification to pass. We require the core discovery
# surface; per-signal tools can vary slightly by deployment version.
_REQUIRED_VERIFY_TOOLS: tuple[str, ...] = ("list_workspaces", "get_gcql_reference")

# Module-level cache for the gcQL reference text, keyed by endpoint, holding
# ``(reference, fetched_monotonic)``. The reference is near-static skill content,
# so caching avoids re-spending tokens/round trips when several tools/sessions
# ask for it in one process. A TTL bounds staleness so a deployment that updates
# its gcQL reference is picked up without a process restart.
_REFERENCE_TTL_SECONDS = 6 * 3600
_REFERENCE_CACHE: dict[str, tuple[str, float]] = {}


@dataclass(frozen=True)
class GroundcoverToolResult:
    """Normalized result of a single groundcover MCP tool call.

    ``data`` is the parsed JSON payload when the tool returned JSON (most signal
    tools return a JSON array/object as text); otherwise it is ``None`` and the
    raw text is available in ``text``.
    """

    success: bool
    tool: str
    data: Any = None
    text: str = ""
    structured: Any = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "tool": self.tool,
            "data": self.data,
            "text": self.text,
            "structured": self.structured,
            "notes": self.notes,
            "error": self.error,
        }


def _root_cause_message(exc: BaseException) -> str:
    """Unwrap ExceptionGroup / __cause__ / __context__ to the underlying error."""
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return _root_cause_message(exc.exceptions[0])
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, BaseException):
        return _root_cause_message(cause)
    context = getattr(exc, "__context__", None)
    if isinstance(context, BaseException):
        return _root_cause_message(context)
    return f"{exc.__class__.__name__}: {exc}"


def _run_async(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except BaseException:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise


def _tool_result_to_payload(result: types.CallToolResult) -> tuple[str, Any]:
    """Flatten an MCP CallToolResult into (text, structured_content)."""
    text_parts: list[str] = []
    for item in result.content:
        if isinstance(item, types.TextContent):
            text_parts.append(item.text)
        elif isinstance(item, types.EmbeddedResource):
            resource = item.resource
            if isinstance(resource, types.TextResourceContents):
                text_parts.append(resource.text)
    structured = result.structured_content
    text_output = "\n".join(part for part in text_parts if part).strip()
    return text_output, structured


class GroundcoverClient:
    """Read-only client over the groundcover public MCP endpoint."""

    def __init__(
        self,
        config: GroundcoverConfig,
        *,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config
        self.timeout = timeout

    # -- properties --------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    def _redact(self, text: str) -> str:
        """Strip the bearer token (and ``Bearer <token>``) from any string."""
        token = self.config.api_key
        if not token:
            return text
        return text.replace(f"Bearer {token}", "Bearer ***").replace(token, "***")

    # -- transport ---------------------------------------------------------

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        from mcp.client.session import ClientSession  # type: ignore[import-not-found]

        stack = AsyncExitStack()
        try:
            read_timeout = max(_DEFAULT_SSE_READ_TIMEOUT_SECONDS, self.timeout)
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=self.config.request_headers,
                    timeout=httpx.Timeout(self.timeout, read=read_timeout),
                )
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(
                    self.config.mcp_url,
                    http_client=http_client,
                    headers=self.config.request_headers,
                    timeout=self.timeout,
                    sse_read_timeout=read_timeout,
                )
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            yield session
        finally:
            await stack.aclose()

    async def _with_connect_retry[T](self, op: Callable[[ClientSession], Awaitable[T]]) -> T:
        """Open a session and run ``op``, retrying only connection-level failures.

        Read-only/idempotent MCP calls (tools-list, query tools) are safe to retry
        on transient connect errors; the retry budget is intentionally tiny.
        """
        last_exc: BaseException | None = None
        for attempt in range(_MAX_CONNECT_RETRIES + 1):
            try:
                async with self._session() as session:
                    return await op(session)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_exc = exc
                if attempt < _MAX_CONNECT_RETRIES:
                    continue
                raise
        # Unreachable, but keeps the type checker satisfied.
        assert last_exc is not None
        raise last_exc

    async def _list_tools_async(self) -> list[str]:
        async def _op(session: ClientSession) -> list[str]:
            result = await session.list_tools()
            return [tool.name for tool in result.tools]

        return await self._with_connect_retry(_op)

    async def _call_tool_async(
        self, tool_name: str, arguments: dict[str, Any] | None
    ) -> GroundcoverToolResult:
        async def _op(session: ClientSession) -> GroundcoverToolResult:
            result = await session.call_tool(tool_name, arguments or {})
            return self._normalize_tool_result(tool_name, result)

        return await self._with_connect_retry(_op)

    def _normalize_tool_result(
        self, tool_name: str, result: types.CallToolResult
    ) -> GroundcoverToolResult:
        text, structured = _tool_result_to_payload(result)
        notes: list[str] = []
        data: Any = None
        # The server appends free-text guidance lines (truncation/empty hints)
        # after a JSON payload. Parse the leading JSON document and keep the
        # trailing prose as notes for the investigator.
        parsed, remainder = _split_json_prefix(text)
        if parsed is not None:
            data = parsed
            if remainder:
                notes.append(self._redact(remainder))
        if result.is_error:
            return GroundcoverToolResult(
                success=False,
                tool=tool_name,
                data=data,
                text=self._redact(text),
                structured=structured,
                notes=notes,
                error=self._redact(text) or "groundcover returned an error",
            )
        return GroundcoverToolResult(
            success=True,
            tool=tool_name,
            data=data,
            text=self._redact(text),
            structured=structured,
            notes=notes,
            error=None,
        )

    # -- sync API ----------------------------------------------------------

    def list_tools(self) -> list[str]:
        """Return the tool names exposed by the configured MCP endpoint."""
        return cast("list[str]", _run_async(self._list_tools_async()))

    def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> GroundcoverToolResult:
        """Call a groundcover MCP tool and normalize the result.

        Never raises for protocol/transport errors — failures are normalized
        into a ``GroundcoverToolResult`` with ``success=False`` and a redacted,
        actionable ``error`` string.
        """
        if not self.is_configured:
            return GroundcoverToolResult(
                success=False,
                tool=tool_name,
                error="groundcover integration not configured (missing api_key or mcp_url).",
            )
        try:
            return cast(
                "GroundcoverToolResult",
                _run_async(self._call_tool_async(tool_name, arguments)),
            )
        except Exception as exc:
            capture_service_error(
                exc,
                logger=logger,
                integration="groundcover",
                method=f"call_tool:{tool_name}",
            )
            return GroundcoverToolResult(
                success=False,
                tool=tool_name,
                error=self._redact(_root_cause_message(exc)),
            )

    def list_workspaces(self) -> dict[str, Any]:
        """Discover the tenants/backends the configured token can query."""
        result = self.call_tool("list_workspaces", {})
        if not result.success:
            return {"success": False, "error": result.error, "workspaces": []}
        workspaces = result.data if isinstance(result.data, list) else []
        return {"success": True, "workspaces": workspaces, "total": len(workspaces)}

    def get_query_reference(self) -> dict[str, Any]:
        """Fetch (and cache, with a TTL) the gcQL reference skill text."""
        cached = _REFERENCE_CACHE.get(self.config.mcp_url)
        if cached is not None and (time.monotonic() - cached[1]) < _REFERENCE_TTL_SECONDS:
            return {"success": True, "reference": cached[0], "cached": True}
        result = self.call_tool("get_gcql_reference", {})
        if not result.success:
            return {"success": False, "error": result.error, "reference": ""}
        reference = result.text or (json.dumps(result.data) if result.data is not None else "")
        if reference:
            _REFERENCE_CACHE[self.config.mcp_url] = (reference, time.monotonic())
        return {"success": True, "reference": reference, "cached": False}

    # -- verification ------------------------------------------------------

    def probe_access(self) -> ProbeResult:
        """Validate the token + endpoint without querying customer telemetry.

        Connects, lists tools to prove the expected public surface exists, then
        lists workspaces. If the account is ambiguous (multiple tenants/backends)
        and routing is not configured, returns an actionable failure naming the
        missing field.
        """
        if not self.is_configured:
            return ProbeResult.missing("Missing groundcover API key or MCP URL.")

        try:
            tools = _run_async(self._list_tools_async())
        except Exception as exc:
            return ProbeResult.failed(
                f"Could not connect to groundcover MCP: {self._redact(_root_cause_message(exc))}"
            )

        missing_tools = [name for name in _REQUIRED_VERIFY_TOOLS if name not in tools]
        if missing_tools:
            return ProbeResult.failed(
                "Connected to groundcover MCP but expected read-only tools are missing: "
                f"{', '.join(missing_tools)}. Check that the token is a read-only "
                "service-account token with MCP access."
            )

        workspaces_result = self.list_workspaces()
        if not workspaces_result.get("success"):
            return ProbeResult.failed(
                f"Listed MCP tools but workspace discovery failed: {workspaces_result.get('error')}"
            )

        workspaces = workspaces_result.get("workspaces", [])
        if not workspaces:
            return ProbeResult.failed(
                "Connected to groundcover MCP, but no workspaces are accessible with this "
                "token. Check that the service-account token is scoped to a tenant."
            )
        problem = self._routing_problem(workspaces)
        if problem:
            return ProbeResult.failed(problem)

        return ProbeResult.passed(
            self._probe_success_detail(tools, workspaces),
            tools=len(tools),
            workspaces=len(workspaces),
        )

    def _routing_problem(self, workspaces: list[Any]) -> str | None:
        """Return an actionable message when routing is missing, ambiguous, or wrong.

        Catches three failure modes: (a) an account with multiple workspaces and
        no tenant selected, (b) a configured tenant/backend that does not exist
        in the account (mistyped value), and (c) a tenant with multiple backends
        and no backend selected.
        """
        tenants = [w for w in workspaces if isinstance(w, dict)]

        # (b) configured tenant must actually exist.
        if self.config.tenant_uuid:
            matched = [t for t in tenants if t.get("tenant_uuid") == self.config.tenant_uuid]
            if not matched:
                options = ", ".join(
                    f"{t.get('org_name', '?')} ({t.get('tenant_uuid', '?')})" for t in tenants
                )
                return (
                    f"Configured GROUNDCOVER_TENANT_UUID '{self.config.tenant_uuid}' is not one of "
                    f"your workspaces. Available: {options or 'none'}"
                )
            tenants = matched
        elif len(tenants) > 1:
            # (a) ambiguous: multiple workspaces, none selected.
            options = ", ".join(
                f"{t.get('org_name', '?')} ({t.get('tenant_uuid', '?')})" for t in tenants
            )
            return (
                f"Account has {len(tenants)} workspaces; set GROUNDCOVER_TENANT_UUID to "
                f"select one. Available: {options}"
            )

        for tenant in tenants:
            backends = tenant.get("backends") or []
            if not isinstance(backends, list):
                continue
            if self.config.backend_id:
                # (b) configured backend must exist in the selected tenant.
                if self.config.backend_id not in [str(b) for b in backends]:
                    return (
                        f"Configured GROUNDCOVER_BACKEND_ID '{self.config.backend_id}' is not a "
                        f"backend of workspace {tenant.get('org_name', '?')}. Available: "
                        f"{', '.join(str(b) for b in backends) or 'none'}"
                    )
            elif len(backends) > 1:
                # (c) ambiguous: multiple backends, none selected.
                return (
                    f"Workspace {tenant.get('org_name', '?')} has {len(backends)} backends; "
                    f"set GROUNDCOVER_BACKEND_ID to select one. Available: "
                    f"{', '.join(str(b) for b in backends)}"
                )
        return None

    def _probe_success_detail(self, tools: list[str], workspaces: list[Any]) -> str:
        host = httpx.URL(self.config.mcp_url).host
        parts = [f"Connected to {host}", f"{len(tools)} MCP tools available"]
        if workspaces:
            first = workspaces[0]
            if isinstance(first, dict):
                parts.append(f"workspace: {first.get('org_name', '?')}")
        return "; ".join(parts) + "."


def _split_json_prefix(text: str) -> tuple[Any, str]:
    """Parse a leading JSON document from ``text``; return (parsed, remainder).

    groundcover signal tools return a JSON array/object followed by optional
    free-text guidance lines. ``json.JSONDecoder.raw_decode`` parses the JSON
    prefix and reports where it ended so we can keep the trailing prose.
    """
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "[{":
        return None, ""
    decoder = json.JSONDecoder()
    try:
        parsed, end = decoder.raw_decode(stripped)
    except json.JSONDecodeError:
        return None, ""
    remainder = stripped[end:].strip()
    return parsed, remainder
