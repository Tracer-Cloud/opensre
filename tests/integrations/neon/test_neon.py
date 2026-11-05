"""Neon integration: config resolution, API client, diagnostics, tools, and wiring."""

from __future__ import annotations

import json
from collections.abc import Iterator
from http import HTTPStatus
from typing import Any

import httpx
import pytest

from config.constants.neon import NEON_API_KEY_ENV, NEON_BRANCH_ID_ENV, NEON_PROJECT_ID_ENV
from core.domain.types.evidence import CATALOG_ENTRIES_KEY
from core.domain.types.tools import ToolSurface
from core.llm.types import ToolCall
from core.tool.execution import execute_tools
from integrations.neon import build_neon_config, classify
from integrations.neon.client import NeonAPIError, NeonClient
from integrations.neon.diagnostics import (
    get_project_status,
    get_storage_buckets,
    list_storage_objects,
)
from integrations.neon.tools.neon_project_status_tool.tool import map_neon_project_status
from integrations.neon.tools.neon_storage_buckets_tool.tool import map_neon_storage_buckets
from integrations.neon.tools.neon_storage_objects_tool.tool import map_neon_storage_objects
from tests.integrations.neon import fixtures as fx

API_KEY = "napi_test_key_do_not_log"
NEON_TOOL_NAMES = {
    "get_neon_project_status",
    "get_neon_storage_buckets",
    "list_neon_storage_objects",
}


class _FakeNeonAPI:
    """httpx transport serving captured Neon payloads and recording requests."""

    def __init__(
        self,
        routes: dict[str, dict[str, Any]] | None = None,
        *,
        status_code: int = HTTPStatus.OK,
        body: Any = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.routes = fx.ROUTES if routes is None else routes
        self.status_code = status_code
        self.body = body
        self.raise_exc = raise_exc
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.status_code != HTTPStatus.OK:
            return httpx.Response(self.status_code, json=self.body or {})
        if request.url.params.get("prefix") == "logs/":
            return httpx.Response(HTTPStatus.OK, json=fx.OBJECTS_PAGE)
        payload = self.routes.get(request.url.path)
        if payload is None:
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": "not found"})
        return httpx.Response(HTTPStatus.OK, json=payload)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _client(api: _FakeNeonAPI, **overrides: Any) -> NeonClient:
    config = build_neon_config({"api_key": API_KEY, "project_id": fx.PROJECT_ID, **overrides})
    return NeonClient(config, transport=api.transport)


@pytest.fixture
def neon_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
    monkeypatch.setenv(NEON_API_KEY_ENV, API_KEY)
    monkeypatch.setenv(NEON_PROJECT_ID_ENV, fx.PROJECT_ID)
    monkeypatch.delenv(NEON_BRANCH_ID_ENV, raising=False)
    yield


# --------------------------------------------------------------------------- config


def test_classify_requires_key_and_project_and_normalizes() -> None:
    config, service = classify(
        {"api_key": f" {API_KEY} ", "project_id": f" {fx.PROJECT_ID}\n", "branch_id": ""},
        "neon-1",
    )
    assert service == "neon"
    assert config is not None
    assert (config.api_key, config.project_id, config.integration_id) == (
        API_KEY,
        fx.PROJECT_ID,
        "neon-1",
    )
    assert config.base_url == "https://console.neon.tech/api/v2"
    assert classify({"api_key": API_KEY}, "neon-2") == (None, None)
    assert classify({"project_id": fx.PROJECT_ID}, "neon-3") == (None, None)


def test_env_loader_and_detection_publish_neon(
    neon_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.catalog import (
        classify_integrations,
        load_env_integration_services,
        load_env_integrations,
    )

    monkeypatch.setenv(NEON_BRANCH_ID_ENV, fx.MAIN_BRANCH)
    records = [record for record in load_env_integrations() if record["service"] == "neon"]
    assert len(records) == 1
    assert records[0]["credentials"] == {
        "api_key": API_KEY,
        "project_id": fx.PROJECT_ID,
        "branch_id": fx.MAIN_BRANCH,
    }
    resolved = classify_integrations(records)
    assert resolved["neon"].branch_id == fx.MAIN_BRANCH
    assert "neon" in load_env_integration_services()

    monkeypatch.delenv(NEON_PROJECT_ID_ENV)
    assert not [record for record in load_env_integrations() if record["service"] == "neon"]


# --------------------------------------------------------------------------- client


def test_client_sends_bearer_key_to_project_scoped_paths() -> None:
    api = _FakeNeonAPI()
    with _client(api) as client:
        assert client.get_project()["name"] == "opensre-neon-test"
        client.list_operations(5000)
    first, second = api.requests
    assert first.headers["Authorization"] == f"Bearer {API_KEY}"
    assert first.url.path == f"/api/v2/projects/{fx.PROJECT_ID}"
    assert second.url.params["limit"] == "1000"


@pytest.mark.parametrize(
    "bad",
    ["../other-project", "a/b", "bucket?x=1", "", "x" * 200, ".hidden"],
)
def test_untrusted_identifiers_never_reach_the_network(bad: str) -> None:
    api = _FakeNeonAPI()
    with _client(api) as client, pytest.raises(NeonAPIError) as err:
        client.list_objects(fx.MAIN_BRANCH, bad)
    assert err.value.invalid_input is True
    assert api.requests == []


def test_branch_resolution_prefers_explicit_then_configured_then_default() -> None:
    api = _FakeNeonAPI()
    assert _client(api).resolve_branch_id("br-explicit-1") == "br-explicit-1"
    assert _client(api, branch_id="br-configured-1").resolve_branch_id() == "br-configured-1"
    assert api.requests == []
    assert _client(api).resolve_branch_id() == fx.MAIN_BRANCH
    assert api.requests[-1].url.path.endswith("/branches")


@pytest.mark.parametrize(
    ("status", "failure_state", "message"),
    [
        (HTTPStatus.UNAUTHORIZED, "denied", "rejected the API key"),
        (HTTPStatus.FORBIDDEN, "denied", "cannot access"),
        (HTTPStatus.NOT_FOUND, "no_match", "not found"),
        (HTTPStatus.TOO_MANY_REQUESTS, "transport_failure", "rate limit"),
        (HTTPStatus.SERVICE_UNAVAILABLE, "transport_failure", "unavailable"),
    ],
)
def test_api_errors_become_structured_failures(
    status: HTTPStatus, failure_state: str, message: str
) -> None:
    api = _FakeNeonAPI(status_code=status, body={"message": "details from neon"})
    result = get_project_status(_client(api))
    assert result["available"] is False
    assert result["failure_state"] == failure_state
    assert message in result["error"]
    assert API_KEY not in json.dumps(result)


def test_transport_errors_are_redacted_and_reported() -> None:
    api = _FakeNeonAPI(raise_exc=httpx.ConnectError(f"refused while sending {API_KEY}"))
    result = get_storage_buckets(_client(api))
    assert result["failure_state"] == "transport_failure"
    assert API_KEY not in result["error"]
    assert "<redacted>" in result["error"]


def test_probe_reports_project_or_missing_config() -> None:
    probe = _client(_FakeNeonAPI()).probe_access()
    assert probe.ok
    assert fx.PROJECT_ID in probe.detail
    assert not NeonClient(build_neon_config({"api_key": API_KEY})).probe_access().ok


# --------------------------------------------------------------------------- diagnostics


def test_project_status_surfaces_failed_pending_and_disabled_computes() -> None:
    result = get_project_status(_client(_FakeNeonAPI()))

    assert result["project"]["pg_version"] == 18
    assert result["total_branches"] == 2
    assert [row["action"] for row in result["failed_operations"]] == ["start_compute"]
    assert result["failed_operations"][0]["error"] == "compute quota exceeded"
    assert [row["action"] for row in result["pending_operations"]] == ["create_timeline"]
    assert result["active_endpoints"] == []
    assert result["disabled_endpoints"] == ["ep-young-sun-q9w8e7r6"]
    main = next(row for row in result["endpoints"] if row["id"] == fx.ENDPOINT_ID)
    assert main["current_state"] == "idle"
    assert main["suspended_at"] == "2026-11-05T10:31:15Z"


def test_project_status_branch_filter_narrows_computes_and_operations() -> None:
    result = get_project_status(_client(_FakeNeonAPI()), branch_id=fx.MAIN_BRANCH)
    assert {row["branch_id"] for row in result["endpoints"]} == {fx.MAIN_BRANCH}
    assert [row["action"] for row in result["operations"]] == ["suspend_compute"]
    assert result["failed_operations"] == []
    assert result["total_branches"] == 2


def test_storage_buckets_flags_public_buckets_on_default_branch() -> None:
    result = get_storage_buckets(_client(_FakeNeonAPI()))
    assert result["branch_id"] == fx.MAIN_BRANCH
    assert result["storage_enabled"] is True
    assert result["s3_endpoint"] == fx.STORAGE["s3_endpoint"]
    assert result["public_buckets"] == ["public-assets"]


def test_storage_disabled_branch_skips_bucket_listing() -> None:
    routes = dict(fx.ROUTES)
    routes[f"/api/v2/projects/{fx.PROJECT_ID}/branches/{fx.MAIN_BRANCH}/storage"] = {
        "enabled": False
    }
    api = _FakeNeonAPI(routes)
    result = get_storage_buckets(_client(api, branch_id=fx.MAIN_BRANCH))
    assert result["storage_enabled"] is False
    assert result["buckets"] == []
    assert not any(request.url.path.endswith("/buckets") for request in api.requests)


def test_object_listing_folds_folders_and_paginates() -> None:
    api = _FakeNeonAPI()
    folded = list_storage_objects(_client(api), bucket="incident-artifacts")
    assert folded["folders"] == ["logs/"]
    assert [item["key"] for item in folded["objects"]] == ["root.txt"]
    assert api.requests[-1].url.params["delimiter"] == "/"

    page = list_storage_objects(
        _client(api), bucket="incident-artifacts", prefix="logs/", recursive=True, limit=2
    )
    assert "delimiter" not in api.requests[-1].url.params
    assert api.requests[-1].url.params["limit"] == "2"
    assert page["truncated"] is True
    assert page["next_cursor"] == "logs/2026-11-05/worker-1.txt"


def test_unknown_bucket_is_a_no_match() -> None:
    result = list_storage_objects(_client(_FakeNeonAPI()), bucket="missing-bucket")
    assert result["failure_state"] == "no_match"


# --------------------------------------------------------------------------- evidence


def test_evidence_mappers_cite_incident_signals() -> None:
    evidence: dict[str, Any] = {}
    map_neon_project_status(evidence, get_project_status(_client(_FakeNeonAPI())), {})
    map_neon_storage_buckets(evidence, get_storage_buckets(_client(_FakeNeonAPI())), {})
    map_neon_storage_objects(
        evidence,
        list_storage_objects(_client(_FakeNeonAPI()), bucket="incident-artifacts"),
        {},
    )
    entries = {entry["source"]: entry["summary"] for entry in evidence[CATALOG_ENTRIES_KEY]}
    assert "1 failed operations (start_compute)" in entries["get_neon_project_status"]
    assert "disabled computes: ep-young-sun-q9w8e7r6" in entries["get_neon_project_status"]
    assert entries["get_neon_storage_buckets"] == "2 buckets, public: public-assets"
    assert entries["list_neon_storage_objects"].startswith("1 objects, 1 folders")

    untouched: dict[str, Any] = {}
    map_neon_project_status(untouched, {"available": False, "error": "x"}, {})
    assert untouched == {}


def test_compute_counts_cover_every_endpoint_when_the_list_is_capped() -> None:
    routes = dict(fx.ROUTES)
    routes[f"/api/v2/projects/{fx.PROJECT_ID}/endpoints"] = {
        "endpoints": [
            {"id": f"ep-busy-{index}", "branch_id": fx.MAIN_BRANCH, "current_state": "active"}
            for index in range(60)
        ]
    }
    result = get_project_status(_client(_FakeNeonAPI(routes)))
    assert len(result["endpoints"]) == 50
    assert result["endpoints_truncated"] is True
    assert result["total_endpoints"] == 60

    evidence: dict[str, Any] = {}
    map_neon_project_status(evidence, result, {})
    assert evidence[CATALOG_ENTRIES_KEY][0]["summary"].startswith("60/60 computes active")


# --------------------------------------------------------------------------- tools + runtime


def test_tools_are_registered_on_chat_and_action_with_hidden_credentials() -> None:
    from tools import registry

    for surface in (ToolSurface.CHAT, ToolSurface.ACTION):
        names = {tool.name for tool in registry.get_registered_tools(surface)}
        assert names >= NEON_TOOL_NAMES
    by_name = {tool.name: tool for tool in registry.get_registered_tools()}
    for name in NEON_TOOL_NAMES:
        public = by_name[name].public_input_schema["properties"]
        assert not {"api_key", "project_id", "base_url"} & set(public)
        assert by_name[name].is_available({}) is False


def test_runtime_injects_credentials_and_refuses_model_overrides(
    neon_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.catalog import classify_integrations, load_env_integrations
    from tools import registry

    api = _FakeNeonAPI()
    monkeypatch.setattr(
        "integrations.neon.tools._params.NeonClient",
        lambda config: NeonClient(config, transport=api.transport),
    )
    resolved = classify_integrations(
        [record for record in load_env_integrations() if record["service"] == "neon"]
    )
    tools = [tool for tool in registry.get_registered_tools() if tool.name in NEON_TOOL_NAMES]

    ok, refused = execute_tools(
        [
            ToolCall(id="1", name="get_neon_storage_buckets", input={}),
            ToolCall(
                id="2",
                name="list_neon_storage_objects",
                input={"bucket": "incident-artifacts", "project_id": "someone-elses-project"},
            ),
        ],
        tools,
        resolved,
    )

    assert ok["public_buckets"] == ["public-assets"]
    assert "project_id" in refused["error"]
    assert all(
        request.url.path.startswith(f"/api/v2/projects/{fx.PROJECT_ID}") for request in api.requests
    )
    assert all(request.headers["Authorization"] == f"Bearer {API_KEY}" for request in api.requests)
    assert API_KEY not in json.dumps([ok, refused])


# --------------------------------------------------------------------------- wiring


def test_neon_is_a_verifiable_setup_integration() -> None:
    from integrations.cli import _HANDLERS
    from integrations.neon.setup import NEON_SETUP
    from integrations.registry import INTEGRATION_SPECS, SUPPORTED_SETUP_SERVICES
    from integrations.verification import get_verifier
    from integrations.verify import register_all_verifiers

    register_all_verifiers()
    spec = next(spec for spec in INTEGRATION_SPECS if spec.service == "neon")
    assert spec.has_verifier and spec.direct_effective
    assert "neon" in SUPPORTED_SETUP_SERVICES
    assert "neon" in _HANDLERS
    assert get_verifier("neon") is not None
    fields = {field.name: field for field in NEON_SETUP.fields}
    assert fields["api_key"].secret and fields["api_key"].env_var == NEON_API_KEY_ENV
    assert fields["project_id"].required
    assert not fields["branch_id"].required
