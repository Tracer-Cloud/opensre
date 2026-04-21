"""Tests for the instance-level store APIs: upsert, remove, filter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.integrations.store import (
    get_instance,
    get_instances,
    remove_instance,
    upsert_instance,
)


@pytest.fixture
def tmp_store(tmp_path: Path):
    store_file = tmp_path / "integrations.json"
    with patch("app.integrations.store.STORE_PATH", store_file):
        yield store_file


def _seed(store_file: Path, records: list[dict]) -> None:
    store_file.parent.mkdir(parents=True, exist_ok=True)
    store_file.write_text(json.dumps({"version": 2, "integrations": records}) + "\n")


def test_upsert_instance_appends_to_existing_record(tmp_store: Path) -> None:
    _seed(
        tmp_store,
        [
            {
                "id": "g1",
                "service": "grafana",
                "status": "active",
                "instances": [{"name": "prod", "tags": {}, "credentials": {"endpoint": "a"}}],
            }
        ],
    )
    upsert_instance(
        "grafana",
        {"name": "staging", "tags": {"env": "staging"}, "credentials": {"endpoint": "b"}},
        record_id="g1",
    )
    instances = get_instances("grafana")
    names = {i["name"] for i in instances}
    assert names == {"prod", "staging"}


def test_upsert_instance_updates_by_name_not_position(tmp_store: Path) -> None:
    _seed(
        tmp_store,
        [
            {
                "id": "g1",
                "service": "grafana",
                "status": "active",
                "instances": [
                    {"name": "prod", "tags": {}, "credentials": {"endpoint": "old"}},
                    {"name": "staging", "tags": {}, "credentials": {"endpoint": "s"}},
                ],
            }
        ],
    )
    upsert_instance(
        "grafana",
        {"name": "prod", "tags": {}, "credentials": {"endpoint": "new"}},
        record_id="g1",
    )
    prod = get_instance("grafana", name="prod")
    assert prod is not None
    assert prod["credentials"]["endpoint"] == "new"
    # staging is still there
    staging = get_instance("grafana", name="staging")
    assert staging is not None
    assert staging["credentials"]["endpoint"] == "s"


def test_upsert_instance_creates_new_record_when_no_record_id(tmp_store: Path) -> None:
    upsert_instance(
        "grafana",
        {"name": "prod", "tags": {}, "credentials": {"endpoint": "p"}},
    )
    instances = get_instances("grafana")
    assert len(instances) == 1
    assert instances[0]["name"] == "prod"


def test_get_instance_by_name_returns_only_that_instance(tmp_store: Path) -> None:
    """PR #527 bug #3 regression: filtered get must not leak sibling instances."""
    _seed(
        tmp_store,
        [
            {
                "id": "g1",
                "service": "grafana",
                "status": "active",
                "instances": [
                    {"name": "prod", "tags": {}, "credentials": {"endpoint": "p"}},
                    {"name": "staging", "tags": {}, "credentials": {"endpoint": "s"}},
                    {"name": "dev", "tags": {}, "credentials": {"endpoint": "d"}},
                ],
            }
        ],
    )
    prod = get_instance("grafana", name="prod")
    assert prod is not None
    assert prod["name"] == "prod"
    assert prod["credentials"]["endpoint"] == "p"
    # Result must carry ONLY the prod instance — not a record with siblings
    assert "instances" not in prod  # shape is the instance dict, not the parent record
    assert "staging" not in json.dumps(prod)
    assert "dev" not in json.dumps(prod)


def test_get_instance_by_tag(tmp_store: Path) -> None:
    _seed(
        tmp_store,
        [
            {
                "id": "g1",
                "service": "grafana",
                "status": "active",
                "instances": [
                    {"name": "prod", "tags": {"env": "prod"}, "credentials": {"endpoint": "p"}},
                    {
                        "name": "staging",
                        "tags": {"env": "staging"},
                        "credentials": {"endpoint": "s"},
                    },
                ],
            }
        ],
    )
    match = get_instance("grafana", tags={"env": "staging"})
    assert match is not None
    assert match["name"] == "staging"


def test_remove_instance_persists_when_record_retains_others(tmp_store: Path) -> None:
    """PR #527 P2 regression: removing one instance must save to disk even
    when the parent record is retained."""
    _seed(
        tmp_store,
        [
            {
                "id": "g1",
                "service": "grafana",
                "status": "active",
                "instances": [
                    {"name": "prod", "tags": {}, "credentials": {"endpoint": "p"}},
                    {"name": "staging", "tags": {}, "credentials": {"endpoint": "s"}},
                ],
            }
        ],
    )
    removed = remove_instance("grafana", "prod")
    assert removed is True

    # Reload from disk to confirm persistence.
    data = json.loads(tmp_store.read_text())
    remaining_names = [i["name"] for i in data["integrations"][0]["instances"]]
    assert remaining_names == ["staging"]


def test_remove_last_instance_removes_record(tmp_store: Path) -> None:
    _seed(
        tmp_store,
        [
            {
                "id": "g1",
                "service": "grafana",
                "status": "active",
                "instances": [
                    {"name": "prod", "tags": {}, "credentials": {"endpoint": "p"}},
                ],
            }
        ],
    )
    removed = remove_instance("grafana", "prod")
    assert removed is True

    data = json.loads(tmp_store.read_text())
    assert data["integrations"] == []


def test_remove_instance_returns_false_when_not_found(tmp_store: Path) -> None:
    _seed(tmp_store, [])
    assert remove_instance("grafana", "prod") is False


def test_get_integration_provides_flat_credentials_view(tmp_store: Path) -> None:
    """Backward compat: callers like azure_sql.py / mysql.py / postgresql.py
    read ``record['credentials']`` directly. get_integration must synthesise
    that view from ``instances[0].credentials`` so they keep working."""
    from app.integrations.store import get_integration

    _seed(
        tmp_store,
        [
            {
                "id": "mysql-1",
                "service": "mysql",
                "status": "active",
                "instances": [
                    {
                        "name": "default",
                        "tags": {},
                        "credentials": {"host": "db.example.com", "database": "prod"},
                    }
                ],
            }
        ],
    )
    record = get_integration("mysql")
    assert record is not None
    # v2 shape is still present
    assert "instances" in record
    # AND the flat credentials view for legacy callers
    assert record["credentials"]["host"] == "db.example.com"
    assert record["credentials"]["database"] == "prod"
