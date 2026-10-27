"""Validated non-secret catalog snapshots stored with provider auth metadata."""

from __future__ import annotations

import json
from collections.abc import Sequence

from config.llm_auth.records import resolve_provider_auth_record, save_provider_auth_record_values


def cached_model_catalog(provider: str) -> tuple[tuple[str, str], ...]:
    raw = resolve_provider_auth_record(provider).get("model_catalog", "")
    try:
        values = json.loads(raw)
    except (ValueError, TypeError):
        return ()
    if not isinstance(values, list):
        return ()
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in values[:1000]:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(x, str) and len(x) <= 1024 for x in item)
        ):
            return ()
        model, label = item
        if model not in seen:
            result.append((model, label))
            seen.add(model)
    return tuple(result)


def save_model_catalog(provider: str, models: Sequence[tuple[str, str]]) -> None:
    """Replace one provider snapshot following intentional successful discovery."""
    save_provider_auth_record_values(
        provider,
        {"model_catalog": json.dumps(list(models), ensure_ascii=False)},
        replace_catalog=True,
    )
