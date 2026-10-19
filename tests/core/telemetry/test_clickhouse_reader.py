"""Exercise the actual HTTP adapter and enforce query/data boundaries."""

import json
from http import HTTPStatus

import httpx
import pytest

from core.telemetry.models import MetricMapping, Scope
from integrations.clickhouse.telemetry import ClickHouseEvidenceConfig, ClickHouseEvidenceReader


def test_parameters_are_bound_and_every_signal_has_scope_and_budgets() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            HTTPStatus.OK, json={"data": [{"total": 20, "errors": 4, "latest": 180, "invalid": 0}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    reader = ClickHouseEvidenceReader(ClickHouseEvidenceConfig(), "secret", client=client)
    scope = Scope("payment' OR 1=1 --", "demo", 150, 180)
    sample = reader.measure(scope, MetricMapping())
    reader.evidence(scope)
    assert sample.total == 20
    assert len(requests) == 3
    for request in requests:
        query = request.content.decode()
        assert scope.service not in query
        assert "{service:String}" in query and "{environment:String}" in query
        assert "{start:Float64}" in query and "{end:Float64}" in query
        assert request.url.params["param_service"] == scope.service
        assert request.url.params["readonly"] == "2"
        assert request.url.params["max_execution_time"] == "5"
        assert request.url.params["max_rows_to_read"] == "500000"
        assert "secret" not in str(request.url)
    assert "LIMIT 10" in requests[1].content.decode()
    assert "LIMIT 10" in requests[2].content.decode()


def test_cumulative_counters_and_oversized_payloads_fail_closed() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            HTTPStatus.OK, json={"data": [{"total": 20, "errors": 4, "latest": 180, "invalid": 1}]}
        )

    reader = ClickHouseEvidenceReader(
        ClickHouseEvidenceConfig(),
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    with pytest.raises(ValueError, match="delta"):
        reader.measure(Scope("payment", "demo", 150, 180), MetricMapping())

    def huge(request: httpx.Request) -> httpx.Response:
        return httpx.Response(HTTPStatus.OK, content=json.dumps({"data": ["x" * 100000]}))

    reader = ClickHouseEvidenceReader(
        ClickHouseEvidenceConfig(),
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(huge)),
    )
    with pytest.raises(ValueError, match="limit"):
        reader.evidence(Scope("payment", "demo", 150, 180))


def test_startup_rejects_admin_credentials() -> None:
    def admin(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(HTTPStatus.OK, json={"data": [{"readonly": 0}]})

    reader = ClickHouseEvidenceReader(
        ClickHouseEvidenceConfig(),
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(admin)),
    )
    with pytest.raises(ValueError, match="read-only"):
        reader.verify()
