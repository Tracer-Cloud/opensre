"""Pre-load OpenRCA / Hugging Face CSV telemetry into investigation evidence.

Uses the same stack as ``scripts/query_opensre_telemetry.py``: ``OpenSRECsvGrafanaBackend``
plus ``query_grafana_*`` tool functions so evidence matches normal tool output shapes.
"""

from __future__ import annotations

from typing import Any

from app.integrations.opensre.csv_grafana_backend import OpenSRECsvGrafanaBackend
from app.integrations.opensre.inject import (
    inject_opensre_into_resolved_integrations,
    resolve_opensre_telemetry_dir,
)
from app.nodes.investigate.processing.post_process import (
    _map_grafana_logs,
    _map_grafana_metrics,
    _map_grafana_traces,
)
from app.tools.GrafanaLogsTool import query_grafana_logs
from app.tools.GrafanaMetricsTool import query_grafana_metrics
from app.tools.GrafanaTracesTool import query_grafana_traces


def merge_opensre_seed_into_state(
    raw_alert: dict[str, Any],
    resolved_integrations: dict[str, Any] | None,
    existing_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return LangGraph partial state: ``resolved_integrations`` and merged ``evidence``."""
    merged = inject_opensre_into_resolved_integrations(raw_alert, resolved_integrations)
    if merged is None:
        merged = dict(resolved_integrations or {})

    telemetry_dir = resolve_opensre_telemetry_dir(raw_alert)
    evidence = dict(existing_evidence or {})

    if telemetry_dir is None:
        return {"resolved_integrations": merged, "evidence": evidence}

    backend = OpenSRECsvGrafanaBackend(telemetry_dir=telemetry_dir, alert_fixture=raw_alert)

    evidence.update(
        {
            "opensre_telemetry_dir": str(telemetry_dir),
            "opensre_telemetry_seed": True,
        }
    )
    evidence.update(
        _map_grafana_metrics(
            query_grafana_metrics(
                metric_name="",
                service_name=None,
                grafana_backend=backend,
            )
        )
    )
    evidence.update(
        _map_grafana_logs(
            query_grafana_logs(
                service_name="",
                pipeline_name="",
                execution_run_id=None,
                time_range_minutes=60,
                limit=200,
                grafana_endpoint=None,
                grafana_api_key=None,
                grafana_backend=backend,
            )
        )
    )
    evidence.update(
        _map_grafana_traces(
            query_grafana_traces(
                service_name="",
                execution_run_id=None,
                limit=50,
                grafana_endpoint=None,
                grafana_api_key=None,
                grafana_backend=backend,
            )
        )
    )

    return {"resolved_integrations": merged, "evidence": evidence}
