"""Local telemetry from the tracer-cloud/opensre Hugging Face dataset (OpenRCA-style CSVs)."""

from __future__ import annotations

from app.integrations.opensre.constants import OPENSRE_HF_DATASET_ID
from app.integrations.opensre.csv_grafana_backend import OpenSRECsvGrafanaBackend
from app.integrations.opensre.hf_remote import (
    extract_openrca_scoring_points,
    infer_opensre_telemetry_relative,
    materialize_opensre_telemetry_from_hub,
    stream_opensre_query_alerts,
    strip_scoring_points_from_alert,
)
from app.integrations.opensre.inject import (
    inject_opensre_into_resolved_integrations,
    resolve_opensre_telemetry_dir,
)
from app.integrations.opensre.seed_evidence import merge_opensre_seed_into_state

__all__ = (
    "OPENSRE_HF_DATASET_ID",
    "OpenSRECsvGrafanaBackend",
    "extract_openrca_scoring_points",
    "infer_opensre_telemetry_relative",
    "inject_opensre_into_resolved_integrations",
    "merge_opensre_seed_into_state",
    "materialize_opensre_telemetry_from_hub",
    "resolve_opensre_telemetry_dir",
    "stream_opensre_query_alerts",
    "strip_scoring_points_from_alert",
)
