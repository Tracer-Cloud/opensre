"""Unit tests for the groundcover MCP service client.

The transport is exercised by patching ``GroundcoverClient._session`` with a
fake MCP session, so these tests cover normalization, redaction, JSON/SSE-style
payload parsing, cached query-reference behavior, and verification logic without
any network access.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import types

import integrations.groundcover.client as gc_client
from integrations.groundcover.config import GroundcoverIntegrationConfig


def _config(**overrides: Any) -> GroundcoverIntegrationConfig:
    base: dict[str, Any] = {"api_key": "tok-secret", "mcp_url": "https://mcp.example.com/api/mcp"}
    base.update(overrides)
    return GroundcoverIntegrationConfig.model_validate(base)


def _text_result(text: str, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        is_error=is_error,
    )


def _patch_session(
    monkeypatch: pytest.MonkeyPatch,
    client: gc_client.GroundcoverClient,
    *,
    tools: list[str] | None = None,
    results: dict[str, Any] | None = None,
) -> None:
    """Patch ``client._session`` to yield a fake MCP session."""
    tool_names = tools or []
    tool_results = results or {}

    class _FakeSession:
        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> Any:
            return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in tool_names])

        async def call_tool(self, name: str, _arguments: dict[str, Any]) -> Any:
            outcome = tool_results.get(name)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    @asynccontextmanager
    async def _fake_session() -> Any:
        yield _FakeSession()

    monkeypatch.setattr(client, "_session", _fake_session)


@pytest.fixture(autouse=True)
def _clear_reference_cache() -> None:
    gc_client._REFERENCE_CACHE.clear()


def test_not_configured_returns_unavailable_without_network() -> None:
    client = gc_client.GroundcoverClient(_config(api_key=""))
    result = client.call_tool("query_logs", {"query": "* | limit 10"})
    assert result.success is False
    assert "not configured" in (result.error or "")


def test_call_tool_parses_json_array_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    client = gc_client.GroundcoverClient(_config())
    _patch_session(
        monkeypatch,
        client,
        results={"query_logs": _text_result('[{"level":"error","msg":"boom"}]')},
    )
    result = client.call_tool("query_logs", {"query": "* | limit 10"})
    assert result.success is True
    assert result.data == [{"level": "error", "msg": "boom"}]
    assert result.notes == []


def test_call_tool_keeps_trailing_guidance_as_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = '[{"a":1}]\nResults truncated at 1 rows. Use more specific filters.'
    client = gc_client.GroundcoverClient(_config())
    _patch_session(monkeypatch, client, results={"query_logs": _text_result(payload)})
    result = client.call_tool("query_logs", {"query": "* | limit 1"})
    assert result.data == [{"a": 1}]
    assert result.notes and "truncated" in result.notes[0].lower()


def test_call_tool_error_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    leaky = "auth failed for Bearer tok-secret using tok-secret"
    client = gc_client.GroundcoverClient(_config())
    _patch_session(
        monkeypatch,
        client,
        results={"query_logs": _text_result(leaky, is_error=True)},
    )
    result = client.call_tool("query_logs", {"query": "* | limit 1"})
    assert result.success is False
    assert "tok-secret" not in (result.error or "")
    assert "Bearer ***" in (result.error or "")


def test_get_query_reference_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    client = gc_client.GroundcoverClient(_config())

    class _FakeSession:
        async def initialize(self) -> None:
            return None

        async def call_tool(self, _name: str, _arguments: dict[str, Any]) -> Any:
            calls["n"] += 1
            return _text_result("# gcQL reference")

    @asynccontextmanager
    async def _fake_session() -> Any:
        yield _FakeSession()

    monkeypatch.setattr(client, "_session", _fake_session)

    first = client.get_query_reference()
    second = client.get_query_reference()
    assert first["reference"] == "# gcQL reference"
    assert first["cached"] is False
    assert second["cached"] is True
    assert calls["n"] == 1


def test_list_workspaces_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    workspaces = [{"tenant_uuid": "t1", "org_name": "Acme", "backends": ["prod"]}]
    client = gc_client.GroundcoverClient(_config())
    _patch_session(
        monkeypatch,
        client,
        results={"list_workspaces": _text_result(_json(workspaces))},
    )
    result = client.list_workspaces()
    assert result["success"] is True
    assert result["total"] == 1
    assert result["workspaces"][0]["org_name"] == "Acme"


def test_probe_missing_when_not_configured() -> None:
    probe = gc_client.GroundcoverClient(_config(api_key="")).probe_access()
    assert probe.status == "missing"


def test_probe_fails_when_required_tools_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = gc_client.GroundcoverClient(_config())
    _patch_session(monkeypatch, client, tools=["query_logs"])
    probe = client.probe_access()
    assert probe.status == "failed"
    assert "list_workspaces" in probe.detail


def test_probe_fails_when_no_workspaces(monkeypatch: pytest.MonkeyPatch) -> None:
    client = gc_client.GroundcoverClient(_config())
    _patch_session(
        monkeypatch,
        client,
        tools=["list_workspaces", "get_gcql_reference"],
        results={"list_workspaces": _text_result(_json([]))},
    )
    probe = client.probe_access()
    assert probe.status == "failed"
    assert "no workspaces" in probe.detail.lower()


def test_probe_reports_tenant_ambiguity(monkeypatch: pytest.MonkeyPatch) -> None:
    workspaces = [
        {"tenant_uuid": "t1", "org_name": "Acme", "backends": ["prod"]},
        {"tenant_uuid": "t2", "org_name": "Beta", "backends": ["prod"]},
    ]
    client = gc_client.GroundcoverClient(_config())
    _patch_session(
        monkeypatch,
        client,
        tools=["list_workspaces", "get_gcql_reference"],
        results={"list_workspaces": _text_result(_json(workspaces))},
    )
    probe = client.probe_access()
    assert probe.status == "failed"
    assert "GROUNDCOVER_TENANT_UUID" in probe.detail


def test_probe_reports_backend_ambiguity(monkeypatch: pytest.MonkeyPatch) -> None:
    workspaces = [{"tenant_uuid": "t1", "org_name": "Acme", "backends": ["prod", "staging"]}]
    client = gc_client.GroundcoverClient(_config())
    _patch_session(
        monkeypatch,
        client,
        tools=["list_workspaces", "get_gcql_reference"],
        results={"list_workspaces": _text_result(_json(workspaces))},
    )
    probe = client.probe_access()
    assert probe.status == "failed"
    assert "GROUNDCOVER_BACKEND_ID" in probe.detail


def test_probe_rejects_unknown_configured_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    workspaces = [{"tenant_uuid": "t1", "org_name": "Acme", "backends": ["prod"]}]
    client = gc_client.GroundcoverClient(_config(tenant_uuid="does-not-exist"))
    _patch_session(
        monkeypatch,
        client,
        tools=["list_workspaces", "get_gcql_reference"],
        results={"list_workspaces": _text_result(_json(workspaces))},
    )
    probe = client.probe_access()
    assert probe.status == "failed"
    assert "GROUNDCOVER_TENANT_UUID" in probe.detail
    assert "does-not-exist" in probe.detail


def test_probe_rejects_unknown_configured_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    workspaces = [{"tenant_uuid": "t1", "org_name": "Acme", "backends": ["prod", "staging"]}]
    client = gc_client.GroundcoverClient(_config(tenant_uuid="t1", backend_id="typo"))
    _patch_session(
        monkeypatch,
        client,
        tools=["list_workspaces", "get_gcql_reference"],
        results={"list_workspaces": _text_result(_json(workspaces))},
    )
    probe = client.probe_access()
    assert probe.status == "failed"
    assert "GROUNDCOVER_BACKEND_ID" in probe.detail


def test_probe_passes_with_valid_explicit_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    workspaces = [{"tenant_uuid": "t1", "org_name": "Acme", "backends": ["prod", "staging"]}]
    client = gc_client.GroundcoverClient(_config(tenant_uuid="t1", backend_id="prod"))
    _patch_session(
        monkeypatch,
        client,
        tools=["list_workspaces", "get_gcql_reference", "query_logs"],
        results={"list_workspaces": _text_result(_json(workspaces))},
    )
    probe = client.probe_access()
    assert probe.status == "passed"


def test_probe_passes_single_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    workspaces = [{"tenant_uuid": "t1", "org_name": "Acme", "backends": ["prod"]}]
    client = gc_client.GroundcoverClient(_config())
    _patch_session(
        monkeypatch,
        client,
        tools=["list_workspaces", "get_gcql_reference", "query_logs"],
        results={"list_workspaces": _text_result(_json(workspaces))},
    )
    probe = client.probe_access()
    assert probe.status == "passed"
    # Detail names the connection target + workspace; assert on stable, non-URL
    # tokens (a host-substring check trips CodeQL's URL-sanitization heuristic).
    assert probe.detail.startswith("Connected to ")
    assert "Acme" in probe.detail


def _json(value: Any) -> str:
    import json

    return json.dumps(value)
