from __future__ import annotations

import json
from urllib.parse import quote

from app.agent.tools.clients.grafana.config import get_grafana_config


def build_logql_query(
    service_name: str,
    *,
    correlation_id: str | None = None,
    execution_run_id: str | None = None,
) -> str:
    base = f'{{service_name="{service_name}"}}'
    filters: list[str] = []

    if execution_run_id:
        filters.append(execution_run_id)
    if correlation_id and correlation_id != execution_run_id:
        filters.append(correlation_id)

    for value in filters:
        base += f' |= "{value}"'

    return base


def build_grafana_explore_url(
    *,
    query: str,
    datasource_uid: str,
    instance_url: str,
    from_time: str = "now-1h",
    to_time: str = "now",
) -> str:
    left = [from_time, to_time, datasource_uid, {"expr": query, "refId": "A"}]
    left_param = quote(json.dumps(left, separators=(",", ":")))
    return f"{instance_url.rstrip('/')}/explore?orgId=1&left={left_param}"


def build_grafana_loki_explore_url(
    service_name: str,
    *,
    correlation_id: str | None = None,
    execution_run_id: str | None = None,
    account_id: str | None = None,
) -> str:
    config = get_grafana_config(account_id)
    if not config.instance_url:
        return ""

    query = build_logql_query(
        service_name,
        correlation_id=correlation_id,
        execution_run_id=execution_run_id,
    )
    return build_grafana_explore_url(
        query=query,
        datasource_uid=config.loki_datasource_uid,
        instance_url=config.instance_url,
    )
