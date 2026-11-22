"""Snowflake query tool: only a single read statement ever reaches Snowflake."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.snowflake.tools.snowflake_query_history_tool import query_snowflake_history


def _run(query: str, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], list[str]]:
    sent: list[str] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"data": []}

    def post(_url: str, **kwargs: Any) -> _Response:
        sent.append(kwargs["json"]["statement"])
        return _Response()

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.httpx.post", post
    )
    result = query_snowflake_history(account_identifier="acct", query=query, token="tok")
    return result, sent


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE orders",
        "SELECT 1; DELETE FROM orders",
        "WITH x AS (SELECT 1) INSERT INTO audit SELECT * FROM x",
        "select * from t where id in (select id from t) ; grant role sysadmin to user eve",
        "CALL purge_history()",
        "SELECT SYSTEM$CANCEL_QUERY('01b2')",
        "USE ROLE ACCOUNTADMIN",
        "/* select */ UPDATE orders SET paid = true",
    ],
)
def test_write_or_multi_statement_sql_never_reaches_snowflake(
    query: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, sent = _run(query, monkeypatch)

    assert sent == []
    assert result["available"] is False
    assert "Query refused" in result["error"]


@pytest.mark.parametrize(
    "query",
    [
        "",
        "SELECT query_text FROM t WHERE query_text ILIKE '%delete from%'",
        "-- drop table x\nSELECT GET(payload, 'replace') FROM events",
        'WITH recent AS (SELECT * FROM "update") SELECT * FROM recent',
        "SHOW WAREHOUSES",
    ],
)
def test_read_statements_still_run(query: str, monkeypatch: pytest.MonkeyPatch) -> None:
    result, sent = _run(query, monkeypatch)

    assert result["available"] is True
    assert len(sent) == 1
