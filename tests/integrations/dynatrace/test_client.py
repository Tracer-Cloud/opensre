"""Dynatrace Grail Query API client tests."""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx

from integrations.dynatrace.client import DynatraceClient
from integrations.dynatrace.config import DynatraceIntegrationConfig

_FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "dynatrace" / "log_query_response.json"
)


def _response(status_code: HTTPStatus, payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def _client() -> DynatraceClient:
    return DynatraceClient(
        DynatraceIntegrationConfig(
            tenant_url="https://abc12345.apps.dynatrace.com",
            platform_token="fake-platform-token",
        )
    )


def test_query_executes_then_polls_realistic_log_payload() -> None:
    client = _client()
    fixture = json.loads(_FIXTURE.read_text())
    http_client = MagicMock()
    http_client.request.side_effect = [
        _response(
            HTTPStatus.OK,
            {"state": "RUNNING", "requestToken": "+fake/token==", "progress": 0},
        ),
        _response(HTTPStatus.OK, fixture),
    ]
    client._client = http_client

    result = client.query("fetch logs | limit 1")

    assert result["success"] is True
    assert result["records"][0]["k8s.deployment.name"] == "omnenest-api"
    assert result["metadata"]["grail"]["scannedRecords"] == 20100234
    execute_call, poll_call = http_client.request.call_args_list
    assert execute_call.args[:2] == ("POST", "/platform/storage/query/v1/query:execute")
    assert execute_call.kwargs["json"] == {"query": "fetch logs | limit 1"}
    assert poll_call.args[:2] == ("GET", "/platform/storage/query/v1/query:poll")
    assert poll_call.kwargs["params"] == {"request-token": "+fake/token=="}


def test_query_reports_auth_error_without_exposing_response_body() -> None:
    client = _client()
    http_client = MagicMock()
    http_client.request.return_value = _response(
        HTTPStatus.FORBIDDEN,
        {"error": "secret vendor detail"},
    )
    client._client = http_client

    result = client.query("fetch logs | limit 1")

    assert result == {
        "success": False,
        "error": "Dynatrace API returned HTTP 403.",
        "error_type": "auth_error",
    }


def test_query_distinguishes_timeout_from_empty_results() -> None:
    client = _client()
    http_client = MagicMock()
    http_client.request.side_effect = httpx.ReadTimeout("timed out")
    client._client = http_client

    result = client.query("fetch logs | limit 1")

    assert result["success"] is False
    assert result["error_type"] == "timeout"
