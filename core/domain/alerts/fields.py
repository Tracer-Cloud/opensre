"""Shared alert field precedence and payload-shape helpers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Final

ALERT_BLOCK_KEYS: Final[tuple[str, ...]] = (
    "commonAnnotations",
    "annotations",
    "commonLabels",
    "labels",
)

ALERT_NAME_RAW_KEYS: Final[tuple[str, ...]] = ("alert_name", "title")
ALERT_NAME_CANONICAL_KEYS: Final[tuple[str, ...]] = ("alert_name",)
ALERT_NAME_LABEL_KEYS: Final[tuple[str, ...]] = ("alertname", "alert_name")
ALERT_NAME_ANNOTATION_KEYS: Final[tuple[str, ...]] = ("summary",)

SEVERITY_RAW_KEYS: Final[tuple[str, ...]] = ("severity",)
SEVERITY_CANONICAL_KEYS: Final[tuple[str, ...]] = ("severity",)
SEVERITY_LABEL_KEYS: Final[tuple[str, ...]] = ("severity", "priority")


def dict_value(source: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return a copied dict value from ``source`` or an empty dict."""
    value = source.get(key)
    return dict(value) if isinstance(value, dict) else {}


def first_present(*values: Any) -> Any:
    """Return the first value that is not ``None`` or a blank string."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def first_mapping_value(source: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first present value for ``keys`` from ``source``."""
    return first_present(*(source.get(key) for key in keys))


def alert_labels(raw_alert: Mapping[str, Any]) -> dict[str, Any]:
    return dict_value(raw_alert, "commonLabels") or dict_value(raw_alert, "labels")


def alert_annotations(raw_alert: Mapping[str, Any]) -> dict[str, Any]:
    return dict_value(raw_alert, "commonAnnotations") or dict_value(raw_alert, "annotations")


def canonical_alert(raw_alert: Mapping[str, Any]) -> dict[str, Any]:
    return dict_value(raw_alert, "canonical_alert")


def iter_alert_blocks(raw_alert: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield annotation/label blocks in stable alert-webhook precedence order."""
    for key in ALERT_BLOCK_KEYS:
        value = raw_alert.get(key)
        if isinstance(value, dict):
            yield dict(value)


def alert_name_value(
    raw_alert: Mapping[str, Any],
    *,
    labels: Mapping[str, Any] | None = None,
    annotations: Mapping[str, Any] | None = None,
    canonical: Mapping[str, Any] | None = None,
    fallback: Any = None,
) -> Any:
    return first_present(
        first_mapping_value(raw_alert, ALERT_NAME_RAW_KEYS),
        first_mapping_value(canonical or {}, ALERT_NAME_CANONICAL_KEYS),
        first_mapping_value(labels or {}, ALERT_NAME_LABEL_KEYS),
        first_mapping_value(annotations or {}, ALERT_NAME_ANNOTATION_KEYS),
        fallback,
    )


def severity_value(
    raw_alert: Mapping[str, Any],
    *,
    labels: Mapping[str, Any] | None = None,
    canonical: Mapping[str, Any] | None = None,
    fallback: Any = None,
) -> Any:
    return first_present(
        first_mapping_value(raw_alert, SEVERITY_RAW_KEYS),
        first_mapping_value(canonical or {}, SEVERITY_CANONICAL_KEYS),
        first_mapping_value(labels or {}, SEVERITY_LABEL_KEYS),
        fallback,
    )
