"""Tests for v1 → v2 migration of the integration store."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from app.integrations.store import (
    _VERSION,
    _load_raw,
    _migrate_record_v1_to_v2,
)


def _write_store(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n")


def test_v1_record_with_credentials_migrates_to_v2() -> None:
    record = {
        "id": "grafana-abc",
        "service": "grafana",
        "status": "active",
        "credentials": {"endpoint": "https://example.com", "api_key": "k"},
    }
    migrated = _migrate_record_v1_to_v2(record)
    assert migrated["instances"][0]["name"] == "default"
    assert migrated["instances"][0]["credentials"]["endpoint"] == "https://example.com"
    assert migrated["instances"][0]["credentials"]["api_key"] == "k"
    # Structural fields preserved at top level.
    assert migrated["id"] == "grafana-abc"
    assert migrated["service"] == "grafana"
    assert migrated["status"] == "active"


def test_v1_aws_record_with_top_level_role_arn_migrates_correctly() -> None:
    """PR #527 bug #1 regression: top-level role_arn must move into instance.credentials."""
    record = {
        "id": "aws-1",
        "service": "aws",
        "status": "active",
        "role_arn": "arn:aws:iam::123456789012:role/opensre",
        "external_id": "ext-token",
        "credentials": {"region": "us-east-1"},
    }
    migrated = _migrate_record_v1_to_v2(record)
    creds = migrated["instances"][0]["credentials"]
    assert creds["role_arn"] == "arn:aws:iam::123456789012:role/opensre"
    assert creds["external_id"] == "ext-token"
    assert creds["region"] == "us-east-1"
    # Top-level fields should NOT leak through
    assert "role_arn" not in {
        k for k in migrated if k not in ("id", "service", "status", "instances")
    }


def test_v2_record_passes_through_unchanged() -> None:
    v2 = {
        "id": "grafana-1",
        "service": "grafana",
        "status": "active",
        "instances": [
            {
                "name": "prod",
                "tags": {"env": "prod"},
                "credentials": {"endpoint": "x", "api_key": "k"},
            }
        ],
    }
    migrated = _migrate_record_v1_to_v2(v2)
    assert migrated is v2 or migrated == v2


def test_migration_persists_on_disk_and_is_idempotent(tmp_path: Path) -> None:
    store_file = tmp_path / "integrations.json"
    v1_data = {
        "version": 1,
        "integrations": [
            {
                "id": "g1",
                "service": "grafana",
                "status": "active",
                "credentials": {"endpoint": "https://e", "api_key": "k"},
            }
        ],
    }
    _write_store(store_file, v1_data)

    with patch("app.integrations.store.STORE_PATH", store_file):
        first_load = _load_raw()

    assert first_load["version"] == _VERSION
    assert first_load["integrations"][0]["instances"][0]["credentials"]["api_key"] == "k"

    # The file on disk should now be v2.
    on_disk = json.loads(store_file.read_text())
    assert on_disk["version"] == _VERSION
    assert "instances" in on_disk["integrations"][0]

    # Second load is a no-op (idempotent): content stays identical.
    prior_bytes = store_file.read_bytes()
    with patch("app.integrations.store.STORE_PATH", store_file):
        second_load = _load_raw()
    assert second_load == first_load
    assert store_file.read_bytes() == prior_bytes


def test_malformed_file_yields_empty_v2_store(tmp_path: Path) -> None:
    store_file = tmp_path / "integrations.json"
    store_file.write_text("this is not json")
    with patch("app.integrations.store.STORE_PATH", store_file):
        data = _load_raw()
    assert data == {"version": _VERSION, "integrations": []}


def test_missing_file_yields_empty_v2_store(tmp_path: Path) -> None:
    store_file = tmp_path / "does-not-exist.json"
    with patch("app.integrations.store.STORE_PATH", store_file):
        data = _load_raw()
    assert data == {"version": _VERSION, "integrations": []}


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode not portable to Windows")
def test_permissions_preserved_after_migration(tmp_path: Path) -> None:
    store_file = tmp_path / "integrations.json"
    _write_store(
        store_file,
        {
            "version": 1,
            "integrations": [
                {
                    "id": "g",
                    "service": "grafana",
                    "status": "active",
                    "credentials": {"endpoint": "e", "api_key": "k"},
                }
            ],
        },
    )

    with patch("app.integrations.store.STORE_PATH", store_file):
        _load_raw()  # triggers migration + _save

    mode = stat.S_IMODE(store_file.stat().st_mode)
    assert mode == 0o600
