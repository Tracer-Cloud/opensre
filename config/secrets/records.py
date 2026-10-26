"""Small JSON credential records stored under the same tiers as plain secrets.

A record is a handful of related fields (endpoint + tenant + token, say) that
belong together. It is stored as one JSON value under a ``record:``-prefixed
key, so it inherits the env-then-file policy in :mod:`config.secrets.store`
rather than repeating it.

Not to be confused with ``config.llm_auth.records``, which holds deliberately
non-secret provider status metadata in a plain file so ``opensre auth status``
can answer without reading stored secrets.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

from config.secrets.store import delete_secret, resolve_secret, save_secret

RECORD_PREFIX: Final = "record:"


def _record_key(record_name: str) -> str:
    normalized = record_name.strip()
    if not normalized:
        raise ValueError("record_name must not be empty")
    return f"{RECORD_PREFIX}{normalized}"


def save_llm_credential_record(record_name: str, values: Mapping[str, str]) -> None:
    """Persist a credential record, clearing it when every field is empty."""
    normalized = {
        str(key).strip(): str(value).strip()
        for key, value in values.items()
        if str(key).strip() and str(value).strip()
    }
    if not normalized:
        delete_llm_credential_record(record_name)
        return
    save_secret(_record_key(record_name), json.dumps(normalized, sort_keys=True))


def resolve_llm_credential_record(record_name: str) -> dict[str, str]:
    """Resolve a credential record, or ``{}`` when absent or unreadable."""
    raw = resolve_secret(_record_key(record_name))
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in parsed.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def delete_llm_credential_record(record_name: str) -> None:
    """Remove a credential record from every tier."""
    try:
        key = _record_key(record_name)
    except ValueError:
        return
    delete_secret(key)


__all__ = [
    "RECORD_PREFIX",
    "delete_llm_credential_record",
    "resolve_llm_credential_record",
    "save_llm_credential_record",
]
