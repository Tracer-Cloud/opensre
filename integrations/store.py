"""Local integration credential store.

Integrations are stored in ~/.opensre/integrations.json.

File format (v2 — see ``_migrate_record_v1_to_v2`` for the v1 shape):
{
  "version": 2,
  "integrations": [
    {
      "id": "grafana-1",
      "service": "grafana",
      "status": "active",
      "instances": [
        {
          "name": "prod",
          "tags": {"env": "prod"},
          "credentials": {"endpoint": "https://...", "api_key": "..."}
        },
        {
          "name": "staging",
          "tags": {"env": "staging"},
          "credentials": {"endpoint": "https://...", "api_key": "..."}
        }
      ]
    }
  ]
}

v1 records are auto-migrated on load. Each record's ``credentials`` plus
any non-structural top-level fields (e.g. AWS ``role_arn``) are moved into
a single ``default`` instance. The migration is idempotent; the file is
rewritten with ``version: 2`` on first load.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from config.constants.account import INTEGRATION_LOCAL_ORIGIN, INTEGRATION_RETRIEVAL_ORIGIN_FIELD
from config.constants.paths import integrations_store_path

logger = logging.getLogger(__name__)

# Tests may point this at a concrete Path; unset, the store follows the
# customer whose scope is bound for this turn.
STORE_PATH: Path | None = None


def resolve_store_path() -> Path:
    """Return the effective integration store path for the current scope."""
    return Path(STORE_PATH) if STORE_PATH is not None else integrations_store_path()


_VERSION = 2
_LOCK_TIMEOUT_SECONDS = 10.0

# Structural fields on an integration record — everything else at the top
# level of a v1 record is migrated into the default instance's credentials.
_STRUCTURAL_RECORD_FIELDS = frozenset({"id", "service", "status", "instances"})


class IntegrationStoreLockTimeout(TimeoutError):
    """Raised when the integration store lock cannot be acquired in time."""


def _lock_timeout_error() -> IntegrationStoreLockTimeout:
    return IntegrationStoreLockTimeout(
        f"Integration store locked: {_lock_path()} (store: {resolve_store_path()})"
    )


def _migrate_record_v1_to_v2(record: dict[str, Any]) -> dict[str, Any]:
    """Migrate a single integration record from v1 shape to v2.

    v1 records may carry credentials in ``record["credentials"]`` AND
    additional top-level fields (AWS had ``role_arn`` and ``external_id``
    at the record's top level with an often-empty ``credentials: {}``).
    This migration consolidates EVERYTHING non-structural into
    ``instances[0].credentials`` so downstream code reads one uniform shape.

    v2 records (already containing ``instances``) pass through untouched.
    """
    if isinstance(record.get("instances"), list):
        return record
    credentials = dict(record.get("credentials", {}))
    for key, value in record.items():
        if key in _STRUCTURAL_RECORD_FIELDS or key == "credentials":
            continue
        credentials.setdefault(key, value)
    return {
        "id": record.get("id", ""),
        "service": record.get("service", ""),
        "status": record.get("status", "active"),
        "instances": [{"name": "default", "tags": {}, "credentials": credentials}],
    }


def _migrate_if_needed(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return (possibly-migrated) data and whether migration happened."""
    if data.get("version") == _VERSION:
        return data, False
    records = data.get("integrations", [])
    if not isinstance(records, list):
        records = []
    migrated_records = [_migrate_record_v1_to_v2(r) if isinstance(r, dict) else r for r in records]
    return {"version": _VERSION, "integrations": migrated_records}, True


def _lock_path() -> Path:
    """Return the file lock path derived from the current STORE_PATH."""
    return resolve_store_path().with_suffix(".lock")


def _ensure_private_store_directory(path: Path) -> None:
    """Create the store directory and restrict it to the current user."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        with contextlib.suppress(OSError):
            path.chmod(0o700)


def _acquire_lock() -> FileLock:
    """Create and return a FileLock for the current STORE_PATH."""
    _ensure_private_store_directory(resolve_store_path().parent)
    return FileLock(str(_lock_path()), timeout=_LOCK_TIMEOUT_SECONDS)


def _atomic_write(dest: Path, data: dict[str, Any]) -> None:
    """Write ``data`` to ``dest`` atomically via a temp file + fsync + replace."""
    _ensure_private_store_directory(dest.parent)
    serialized = json.dumps(data, indent=2) + "\n"
    fd: int | None = None
    tmp_path_str: str | None = None
    try:
        fd, tmp_path_str = tempfile.mkstemp(
            dir=dest.parent,
            prefix=dest.name + ".tmp",
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        # os.replace is atomic on POSIX and Windows (Python >=3.3)
        os.replace(tmp_path_str, dest)
    except Exception:
        if tmp_path_str:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path_str)
        raise
    with contextlib.suppress(OSError):
        dest.chmod(0o600)


def _save_unlocked(data: dict[str, Any]) -> None:
    """Persist ``data`` to STORE_PATH without acquiring a lock.

    Callers must already hold the store lock.
    """
    _atomic_write(resolve_store_path(), data)


def _load_raw_unlocked() -> tuple[dict[str, Any], bool]:
    """Read the store from disk and migrate in memory.

    Returns ``(data, did_migrate)``.  This helper does **not** write back
    migrations and does **not** acquire any lock.
    """
    if not resolve_store_path().exists():
        return {"version": _VERSION, "integrations": []}, False
    try:
        text = resolve_store_path().read_text(encoding="utf-8")
        data = json.loads(text)
    except (json.JSONDecodeError, OSError):
        logger.warning(
            "Failed to read integrations store at %s", resolve_store_path(), exc_info=True
        )
        return {"version": _VERSION, "integrations": []}, False
    if not isinstance(data, dict) or "integrations" not in data:
        return {"version": _VERSION, "integrations": []}, False

    return _migrate_if_needed(data)


def _load_raw() -> dict[str, Any]:
    """Read the store, migrating on disk if necessary.

    Lock-free for the common v2 path; acquires the store lock only when a
    v1 migration needs to be persisted.
    """
    data, did_migrate = _load_raw_unlocked()
    if did_migrate:
        try:
            with _acquire_lock():
                # Re-read under lock in case another process already migrated
                data2, did_migrate2 = _load_raw_unlocked()
                if did_migrate2:
                    try:
                        _save_unlocked(data2)
                    except OSError:
                        logger.warning(
                            "Failed to persist v2 migration; continuing with in-memory v2",
                            exc_info=True,
                        )
                return data2
        except Timeout:
            logger.warning(
                "Timed out acquiring integration store lock for v2 migration persist",
                exc_info=True,
            )
            return data
    return data


def _save(data: dict[str, Any]) -> None:
    """Persist ``data`` to STORE_PATH, acquiring the store lock."""
    try:
        with _acquire_lock():
            _save_unlocked(data)
    except Timeout as exc:
        raise _lock_timeout_error() from exc


def _locked_update(mutator: Callable[[dict[str, Any]], bool]) -> tuple[dict[str, Any], bool]:
    """Acquire the store lock, load, mutate, and save atomically.

    The mutator receives the current store data and must return ``True``
    when it actually modified the data so the change is persisted. A v1-to-v2
    migration is persisted even when the mutator itself is a no-op.

    Returns ``(data, changed)``.
    """
    try:
        with _acquire_lock():
            data, did_migrate = _load_raw_unlocked()
            changed = mutator(data)
            if changed or did_migrate:
                _save_unlocked(data)
            return data, changed
    except Timeout as exc:
        raise _lock_timeout_error() from exc


def load_integrations() -> list[dict[str, Any]]:
    """Return all active local integrations (v2 shape)."""
    return [
        {**record, INTEGRATION_RETRIEVAL_ORIGIN_FIELD: INTEGRATION_LOCAL_ORIGIN}
        if record.get("service") == "github"
        else record
        for record in _load_raw().get("integrations", [])
    ]


def replace_integrations(integrations: list[dict[str, Any]]) -> None:
    """Atomically replace the complete local integration set.

    Remote hydration uses this all-or-nothing operation so a failed write
    cannot leave a partially updated credential store behind.
    """
    if any(not isinstance(record, dict) for record in integrations):
        raise ValueError("integrations must contain only objects")
    _save({"version": _VERSION, "integrations": integrations})


def _record_with_flat_credentials_view(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``record`` with a backward-compat ``credentials`` key.

    v2 records store credentials inside ``instances[0].credentials``. Many
    existing callers (``azure_sql.py``, ``mysql.py``, ``postgresql.py``,
    ``surfaces/cli/wizard/flow.py``) read ``record["credentials"]`` directly. This
    helper synthesises a top-level ``credentials`` view from the default
    (first) instance so those callers continue to work unchanged.
    """
    instances = record.get("instances")
    if not isinstance(instances, list) or not instances:
        return record
    first = instances[0] if isinstance(instances[0], dict) else {}
    creds = first.get("credentials", {}) if isinstance(first, dict) else {}
    if not isinstance(creds, dict):
        return record
    view = dict(record)
    view.setdefault("credentials", creds)
    return view


def get_integration(service: str) -> dict[str, Any] | None:
    """Return the first active integration record for a service, or None.

    The returned dict has the v2 shape (``instances`` list) AND a
    synthesised top-level ``credentials`` field mirroring ``instances[0]
    .credentials`` for backward compatibility with callers that read
    ``record["credentials"]`` directly.
    """
    for i in load_integrations():
        if i.get("service") == service and i.get("status") == "active":
            return _record_with_flat_credentials_view(i)
    return None


def _wrap_as_instances(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept a caller's ``entry`` and normalize it to a v2 ``instances`` list.

    - If ``entry`` already has ``instances``, use them.
    - Else, wrap ``entry["credentials"]`` (plus any extra non-structural
      top-level keys) as a single ``default`` instance.
    """
    if isinstance(entry.get("instances"), list):
        return [inst for inst in entry["instances"] if isinstance(inst, dict)]
    credentials = dict(entry.get("credentials", {}))
    for key, value in entry.items():
        if key in _STRUCTURAL_RECORD_FIELDS or key == "credentials":
            continue
        credentials.setdefault(key, value)
    return [{"name": "default", "tags": {}, "credentials": credentials}]


def upsert_integration(service: str, entry: dict[str, Any]) -> None:
    """Add or replace the integration record for a service.

    Accepts v1-shaped entries (``{"credentials": {...}}``) and v2-shaped
    entries (``{"instances": [...]}``) transparently. v1 entries are
    wrapped into a single ``default`` instance.
    """

    def _mutate(data: dict[str, Any]) -> bool:
        integrations: list[dict[str, Any]] = data.get("integrations", [])
        integrations = [i for i in integrations if i.get("service") != service]
        record: dict[str, Any] = {
            "id": entry.get("id") or f"{service}-{uuid.uuid4().hex[:8]}",
            "service": service,
            "status": entry.get("status", "active"),
            "instances": _wrap_as_instances(entry),
        }
        integrations.append(record)
        data["integrations"] = integrations
        return True

    _locked_update(_mutate)


def remove_integration(service: str) -> bool:
    """Remove integration for a service. Returns True if something was removed."""

    def _mutate(data: dict[str, Any]) -> bool:
        before = len(data.get("integrations", []))
        data["integrations"] = [
            i for i in data.get("integrations", []) if i.get("service") != service
        ]
        return len(data["integrations"]) < before

    _, removed = _locked_update(_mutate)
    return removed


def list_integrations() -> list[dict[str, Any]]:
    """Return summary info for all stored integrations."""
    summaries: list[dict[str, Any]] = []
    for i in load_integrations():
        summaries.append(
            {
                "service": i.get("service"),
                "status": i.get("status"),
                "id": i.get("id"),
                "instance_names": [
                    inst.get("name", "default")
                    for inst in i.get("instances", [])
                    if isinstance(inst, dict)
                ],
            }
        )
    return summaries


# ──────────────── Instance-level APIs ──────────────────────────────────────


def get_instances(service: str) -> list[dict[str, Any]]:
    """Return all instance dicts across every record for ``service``.

    Each returned dict has the instance's own ``name``, ``tags``, and
    ``credentials`` plus an ``integration_id`` pointer to its parent record.
    """
    out: list[dict[str, Any]] = []
    for record in load_integrations():
        if record.get("service") != service or record.get("status") != "active":
            continue
        record_id = str(record.get("id", ""))
        for inst in record.get("instances", []):
            if not isinstance(inst, dict):
                continue
            out.append(
                {
                    "name": str(inst.get("name", "default")),
                    "tags": inst.get("tags", {}) or {},
                    "credentials": inst.get("credentials", {}) or {},
                    "integration_id": record_id,
                }
            )
    return out


def _tags_match(inst_tags: dict[str, str], filter_tags: dict[str, str]) -> bool:
    return all(inst_tags.get(key) == value for key, value in filter_tags.items())


def get_instance(
    service: str,
    *,
    name: str | None = None,
    tags: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return the first instance matching ``name`` and/or ``tags``, or None.

    Returns ONLY the matching instance — never leaks sibling instances from
    the same parent record (PR #527 bug #3).
    """
    normalized_name = name.strip().lower() if name else None
    normalized_tags = tags or {}
    for inst in get_instances(service):
        if normalized_name and inst.get("name", "").lower() != normalized_name:
            continue
        if normalized_tags and not _tags_match(inst.get("tags", {}), normalized_tags):
            continue
        return inst
    return None


def upsert_instance(
    service: str,
    instance: dict[str, Any],
    *,
    record_id: str | None = None,
) -> None:
    """Add or update an instance by name within a specific record.

    If ``record_id`` matches an existing record for ``service``, the instance
    is appended or updated by name within that record. Otherwise, a new
    record is created containing only this instance.
    """

    def _mutate(data: dict[str, Any]) -> bool:
        integrations: list[dict[str, Any]] = data.get("integrations", [])
        target: dict[str, Any] | None = None
        for record in integrations:
            if record.get("service") != service:
                continue
            if record_id is not None and record.get("id") != record_id:
                continue
            target = record
            break

        normalized_instance = {
            "name": str(instance.get("name", "default")).strip().lower() or "default",
            "tags": instance.get("tags", {}) or {},
            "credentials": instance.get("credentials", {}) or {},
        }

        if target is None:
            integrations.append(
                {
                    "id": record_id or f"{service}-{uuid.uuid4().hex[:8]}",
                    "service": service,
                    "status": "active",
                    "instances": [normalized_instance],
                }
            )
        else:
            existing_instances = target.get("instances", [])
            if not isinstance(existing_instances, list):
                existing_instances = []
            replaced = False
            for idx, existing in enumerate(existing_instances):
                if (
                    isinstance(existing, dict)
                    and existing.get("name", "").lower() == normalized_instance["name"]
                ):
                    existing_instances[idx] = normalized_instance
                    replaced = True
                    break
            if not replaced:
                existing_instances.append(normalized_instance)
            target["instances"] = existing_instances

        data["integrations"] = integrations
        return True

    _locked_update(_mutate)


def remove_instance(service: str, name: str) -> bool:
    """Remove one named instance from any record for ``service``.

    If removing the instance empties its parent record, the record itself
    is removed. Always persists the change when something was removed
    (PR #527 P2 regression fix).

    Returns True if something was removed.
    """
    normalized_name = name.strip().lower()
    if not normalized_name:
        return False

    def _mutate(data: dict[str, Any]) -> bool:
        integrations: list[dict[str, Any]] = data.get("integrations", [])
        remaining: list[dict[str, Any]] = []
        changed = False

        for record in integrations:
            if record.get("service") != service:
                remaining.append(record)
                continue
            instances = record.get("instances", [])
            if not isinstance(instances, list):
                remaining.append(record)
                continue
            kept = [
                inst
                for inst in instances
                if isinstance(inst, dict) and inst.get("name", "").lower() != normalized_name
            ]
            if len(kept) == len(instances):
                remaining.append(record)
                continue
            changed = True
            if not kept:
                continue  # drop the whole record
            record = dict(record)
            record["instances"] = kept
            remaining.append(record)

        data["integrations"] = remaining
        return changed

    _, changed = _locked_update(_mutate)
    return changed
