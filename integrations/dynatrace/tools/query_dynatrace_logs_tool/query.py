"""Build bounded Dynatrace log queries from structured filters."""

from __future__ import annotations

import json

from config.constants.dynatrace import (
    DYNATRACE_MAX_LOG_LIMIT,
    DYNATRACE_MAX_TIMEFRAME_MINUTES,
    DYNATRACE_SCAN_LIMIT_GB,
)


def _literal(value: str) -> str:
    return json.dumps(value.strip(), ensure_ascii=False)


def build_logs_query(
    *,
    deployment_name: str = "",
    namespace_name: str = "",
    container_name: str = "",
    service_name: str = "",
    log_level: str = "",
    search_text: str = "",
    timeframe_minutes: int,
    limit: int,
) -> tuple[str, int, int]:
    """Return generated DQL plus its bounded timeframe and result limit."""
    bounded_timeframe = max(1, min(int(timeframe_minutes), DYNATRACE_MAX_TIMEFRAME_MINUTES))
    bounded_limit = max(1, min(int(limit), DYNATRACE_MAX_LOG_LIMIT))
    lines = [
        (f"fetch logs, from: -{bounded_timeframe}m, scanLimitGBytes: {DYNATRACE_SCAN_LIMIT_GB:g}")
    ]
    filters: list[str] = []
    if deployment_name.strip():
        filters.append(f"matchesValue(k8s.deployment.name, {_literal(deployment_name)})")
    if namespace_name.strip():
        filters.append(f"matchesValue(k8s.namespace.name, {_literal(namespace_name)})")
    if container_name.strip():
        filters.append(f"matchesValue(k8s.container.name, {_literal(container_name)})")
    if service_name.strip():
        value = _literal(service_name)
        filters.append(
            "("
            + " or ".join(
                (
                    f"matchesValue(service.name, {value})",
                    f"matchesValue(dt.service.name, {value})",
                    f"matchesValue(k8s.service.name, {value})",
                )
            )
            + ")"
        )
    if log_level.strip():
        filters.append(f"matchesValue(loglevel, {_literal(log_level)})")
    if search_text.strip():
        filters.append(
            f"contains(toString(content), {_literal(search_text)}, caseSensitive: false)"
        )
    if filters:
        lines.append(f"| filter {' and '.join(filters)}")
    lines.extend(
        (
            "| sort timestamp desc",
            (
                "| fields timestamp, loglevel, content, service.name, dt.service.name, "
                "k8s.namespace.name, k8s.deployment.name, k8s.container.name, host.name"
            ),
            f"| limit {bounded_limit}",
        )
    )
    return "\n".join(lines), bounded_timeframe, bounded_limit


__all__ = ["build_logs_query"]
