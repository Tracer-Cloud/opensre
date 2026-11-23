"""Splunk log search: SPL that writes, sends or reads secrets never reaches Splunk."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.splunk.client import SplunkClient, SplunkConfig


def _search(query: str, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], list[str]]:
    sent: list[str] = []

    class _Response:
        text = ""

        def raise_for_status(self) -> None:
            return None

    def post(_url: str, **kwargs: Any) -> _Response:
        sent.append(kwargs["data"]["search"])
        return _Response()

    monkeypatch.setattr("integrations.splunk.client.httpx.post", post)
    client = SplunkClient(SplunkConfig(base_url="https://splunk.example:8089", token="t"))
    return client.search_logs(query), sent


@pytest.mark.parametrize(
    "query",
    [
        "index=main error | outputlookup stolen.csv",
        "index=main | collect index=summary",
        "index=main | delete",
        "| rest /services/storage/passwords | table clear_password",
        "index=main | sendemail to=attacker@example.com",
        "index=main [ search index=app | outputlookup x.csv ]",
        "index=main | SCRIPT python payload",
        'index=main | map search="search index=other | delete"',
        "index=main | ``` comment ``` delete",
        "index=main |```multi\nline```outputlookup x.csv",
    ],
)
def test_unsafe_spl_never_reaches_splunk(query: str, monkeypatch: pytest.MonkeyPatch) -> None:
    result, sent = _search(query, monkeypatch)

    assert sent == []
    assert result["success"] is False
    assert "Query refused" in result["error"]


@pytest.mark.parametrize(
    "query",
    [
        "index=main error | head 50",
        "delete error",
        "run timeout | stats count by host",
        'index=main message="| delete everything" | stats count by host',
        "index=main ``` | delete is only mentioned here ``` | head 10",
        "index=main [ search index=app error | fields trace_id ] | table _time message",
    ],
)
def test_investigation_searches_still_run(query: str, monkeypatch: pytest.MonkeyPatch) -> None:
    result, sent = _search(query, monkeypatch)

    assert result["success"] is True
    assert len(sent) == 1
