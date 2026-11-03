"""Request-contract tests for Prefect flow-run log retrieval."""

from __future__ import annotations

import json

import httpx

from integrations.prefect.client import PrefectClient, PrefectConfig


def test_flow_run_logs_pages_recent_entries_with_valid_page_sizes() -> None:
    requests: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/logs/filter"
        body: dict[str, object] = json.loads(request.content)
        requests.append(body)
        offset = int(body["offset"])
        size = int(body["limit"])
        return httpx.Response(
            200,
            json=[
                {"timestamp": str(300 - index), "message": f"line {300 - index}"}
                for index in range(offset, min(offset + size, 300))
            ],
        )

    client = PrefectClient(PrefectConfig(api_url="https://prefect.example/api"))
    client._client = httpx.Client(
        base_url=client.config.base_url,
        transport=httpx.MockTransport(respond),
    )
    with client:
        result = client.get_flow_run_logs("flow-1", limit=250)

    assert result["success"] is True
    assert result["total"] == 250
    assert result["logs"][0]["message"] == "line 51"
    assert result["logs"][-1]["message"] == "line 300"
    assert [(body["offset"], body["limit"]) for body in requests] == [
        (0, 100),
        (100, 100),
        (200, 50),
    ]
    assert all(body["sort"] == "TIMESTAMP_DESC" for body in requests)
    assert all(body["logs"] == {"flow_run_id": {"any_": ["flow-1"]}} for body in requests)
