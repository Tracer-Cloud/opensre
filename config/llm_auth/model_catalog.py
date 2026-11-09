"""Non-secret snapshots of models discovered during provider connection."""

from __future__ import annotations

import json
from collections.abc import Mapping

from config.llm_auth.records import resolve_provider_auth_record, save_provider_auth_record_values


def saved_models(
    provider: str, *, record: Mapping[str, str] | None = None
) -> list[tuple[str, str]]:
    """Return valid cached choices without resolving credentials or network access."""
    metadata = resolve_provider_auth_record(provider) if record is None else record
    raw = metadata.get("model_catalog", "[]")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [
        (item[0], item[1])
        for item in data
        if isinstance(item, list)
        and len(item) == 2
        and all(isinstance(value, str) for value in item)
        and item[0]
    ]


def save_models(provider: str, models: list[tuple[str, str]]) -> None:
    """Keep one catalog with its provider's existing non-secret auth record."""
    record = resolve_provider_auth_record(provider)
    save_provider_auth_record_values(provider, {**record, "model_catalog": json.dumps(models)})
