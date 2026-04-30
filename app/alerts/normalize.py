"""Canonical OpenSRE alert payload normalization.

This module converts source-specific alert fields into a stable in-memory
shape so downstream nodes can consume one format regardless of origin.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_tags(value: Any) -> dict[str, str]:
    """Parse Datadog-like tags into a dictionary.

    Supports:
    - comma-separated strings: "env:prod,service:payments"
    - list[str]: ["env:prod", "service:payments"]
    - dict[str, Any]: {"env": "prod"}
    """
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if _to_text(k) and _to_text(v)}

    items: Iterable[str]
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list):
        items = [str(part).strip() for part in value if _to_text(part)]
    else:
        return {}

    parsed: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            continue
        key, raw_value = item.split(":", 1)
        key_text = _to_text(key)
        value_text = _to_text(raw_value)
        if key_text and value_text:
            parsed[key_text] = value_text
    return parsed


def _coerce_pid(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        pid = int(value)
        return pid if pid >= 0 else None
    text = _to_text(value)
    if text is None:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid >= 0 else None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def normalize_alert_payload(raw_alert: dict[str, Any]) -> dict[str, Any]:
    """Normalize an alert payload to canonical OpenSRE alert format.

    The returned payload preserves original fields and also adds:
    - ``commonLabels`` / ``commonAnnotations`` as dictionaries
    - top-level ``process_name`` / ``cmdline`` / ``pid`` when discovered
    - ``canonical_alert`` containing the normalized, vendor-agnostic shape
    """
    normalized = dict(raw_alert)

    raw_common_labels = normalized.get("commonLabels")
    labels = (
        _as_mapping(raw_common_labels)
        if raw_common_labels is not None
        else _as_mapping(normalized.get("labels"))
    )

    tags = _parse_tags(normalized.get("tags"))
    if tags:
        labels = {**tags, **labels}

    raw_common_annotations = normalized.get("commonAnnotations")
    annotations = (
        _as_mapping(raw_common_annotations)
        if raw_common_annotations is not None
        else _as_mapping(normalized.get("annotations"))
    )

    normalized["commonLabels"] = labels
    normalized["commonAnnotations"] = annotations

    process_name = _to_text(
        _first_present(
            normalized.get("process_name"),
            normalized.get("processName"),
            normalized.get("process.name"),
            normalized.get("procname"),
            labels.get("process_name"),
            labels.get("process"),
            annotations.get("process_name"),
        )
    )
    cmdline = _to_text(
        _first_present(
            normalized.get("cmdline"),
            normalized.get("command"),
            normalized.get("command_line"),
            normalized.get("process.cmdline"),
            normalized.get("process_command_line"),
            labels.get("cmdline"),
            annotations.get("cmdline"),
        )
    )
    pid = _coerce_pid(
        _first_present(
            normalized.get("pid"),
            normalized.get("process_id"),
            normalized.get("process.pid"),
            labels.get("pid"),
            annotations.get("pid"),
        )
    )

    if process_name and not _to_text(normalized.get("process_name")):
        normalized["process_name"] = process_name
    if cmdline and not _to_text(normalized.get("cmdline")):
        normalized["cmdline"] = cmdline
    if pid is not None and _coerce_pid(normalized.get("pid")) is None:
        normalized["pid"] = pid

    canonical_alert = {
        "schema": "opensre.alert.v1",
        "alert_name": _to_text(
            _first_present(
                normalized.get("alert_name"),
                normalized.get("title"),
                labels.get("alertname"),
                labels.get("alert_name"),
            )
        ),
        "pipeline_name": _to_text(
            _first_present(
                normalized.get("pipeline_name"),
                labels.get("pipeline_name"),
                labels.get("pipeline"),
                labels.get("service"),
            )
        ),
        "severity": _to_text(
            _first_present(
                normalized.get("severity"),
                labels.get("severity"),
                labels.get("priority"),
            )
        ),
        "alert_source": _to_text(normalized.get("alert_source")),
        "labels": dict(labels),
        "annotations": dict(annotations),
        "process": {
            "name": process_name,
            "cmdline": cmdline,
            "pid": pid,
        },
    }
    normalized["canonical_alert"] = canonical_alert
    return normalized
