"""Merge tracer-cloud/opensre CSV telemetry into resolved Grafana integrations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.integrations.opensre.csv_grafana_backend import OpenSRECsvGrafanaBackend


def _annotation_dict(raw_alert: dict[str, Any]) -> dict[str, Any]:
    nested = raw_alert.get("annotations") or raw_alert.get("commonAnnotations") or {}
    if not isinstance(nested, dict):
        nested = {}
    merged = {**nested, **{k: v for k, v in raw_alert.items() if v and k not in nested}}
    return merged


def _dataset_root(ann: dict[str, Any], raw_alert: dict[str, Any]) -> str:
    """Resolve local clone root for tracer-cloud/opensre (or OpenRCA-style exports)."""
    for key in (
        "opensre_dataset_root",
        "openrca_dataset_root",
    ):
        v = ann.get(key) or raw_alert.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return (
        os.environ.get("OPENSRE_DATASET_ROOT", "").strip()
        or os.environ.get("OPENRCA_DATASET_ROOT", "").strip()
    )


def _hf_dataset_id(ann: dict[str, Any], raw_alert: dict[str, Any]) -> str | None:
    """Hub repo id for telemetry materialization; unset avoids any network access."""
    for key in (
        "opensre_hf_dataset_id",
        "openrca_hf_dataset_id",
    ):
        v = ann.get(key) or raw_alert.get(key)
        if v and str(v).strip():
            return str(v).strip()
    env_id = os.environ.get("OPENSRE_HF_DATASET_ID", "").strip()
    return env_id or None


def _telemetry_relative(ann: dict[str, Any], raw_alert: dict[str, Any]) -> str | None:
    rel = (
        ann.get("opensre_telemetry_relative")
        or raw_alert.get("opensre_telemetry_relative")
        or ann.get("openrca_telemetry_relative")
        or raw_alert.get("openrca_telemetry_relative")
    )
    if rel and str(rel).strip():
        return str(rel).strip().lstrip("/")
    meta = raw_alert.get("_meta")
    if isinstance(meta, dict):
        meta_rel = meta.get("telemetry_relative")
        if meta_rel and str(meta_rel).strip():
            return str(meta_rel).strip().lstrip("/")
    if os.environ.get("OPENSRE_INFER_TELEMETRY", "").strip().lower() in ("0", "false", "no"):
        return None
    from app.integrations.opensre.hf_remote import infer_opensre_telemetry_relative

    return infer_opensre_telemetry_relative(raw_alert)


def resolve_opensre_telemetry_dir(raw_alert: dict[str, Any]) -> Path | None:
    """Return the absolute telemetry directory (the ``.../telemetry/YYYY_MM_DD`` folder)."""
    ann = _annotation_dict(raw_alert)
    direct = (
        ann.get("opensre_telemetry_dir")
        or raw_alert.get("opensre_telemetry_dir")
        or ann.get("openrca_telemetry_dir")
        or raw_alert.get("openrca_telemetry_dir")
    )
    if direct:
        p = Path(str(direct).strip()).expanduser()
        return p if p.is_dir() else None

    rel = _telemetry_relative(ann, raw_alert)
    root = _dataset_root(ann, raw_alert)
    if rel and root:
        p = (Path(str(root).strip()).expanduser() / str(rel).strip().lstrip("/")).resolve()
        return p if p.is_dir() else None

    hf_id = _hf_dataset_id(ann, raw_alert)
    if rel and hf_id and not (os.environ.get("OPENSRE_DISABLE_HF_TELEMETRY", "").strip()):
        from app.integrations.opensre.hf_remote import materialize_opensre_telemetry_from_hub

        rev = (
            ann.get("opensre_hf_revision")
            or raw_alert.get("opensre_hf_revision")
            or ann.get("openrca_hf_revision")
            or raw_alert.get("openrca_hf_revision")
            or os.environ.get("OPENSRE_HF_REVISION")
            or None
        )
        try:
            return materialize_opensre_telemetry_from_hub(
                dataset_id=hf_id,
                telemetry_relative=rel,
                revision=rev,
            )
        except ImportError:
            return None
    return None


def inject_opensre_into_resolved_integrations(
    raw_alert: dict[str, Any],
    resolved_integrations: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """When the alert carries opensre paths, inject a Grafana integration with CSV backend.

    Skips only when ``grafana`` already has a ``_backend`` (e.g. another fixture backend).
    """
    telemetry_dir = resolve_opensre_telemetry_dir(raw_alert)
    if telemetry_dir is None:
        return resolved_integrations

    base = dict(resolved_integrations or {})
    existing = base.get("grafana")
    if isinstance(existing, dict) and existing.get("_backend") is not None:
        return resolved_integrations

    backend = OpenSRECsvGrafanaBackend(telemetry_dir=telemetry_dir, alert_fixture=raw_alert)
    base["grafana"] = {
        "endpoint": "",
        "api_key": "",
        "connection_verified": True,
        "_backend": backend,
    }
    return base
