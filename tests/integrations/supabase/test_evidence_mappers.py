"""Evidence mapper tests for the Supabase tools."""

from http import HTTPStatus

from integrations.supabase.tools.supabase_health_tool import (
    _map_get_supabase_service_health,
)
from integrations.supabase.tools.supabase_storage_tool import (
    _map_get_supabase_storage_buckets,
)


def test_service_health_mapper_records_entry_when_all_healthy() -> None:
    evidence: dict[str, object] = {}
    output = {
        "services": {
            "postgrest": {"healthy": True, "status_code": HTTPStatus.OK},
            "auth": {"healthy": True, "status_code": HTTPStatus.OK},
            "storage": {"healthy": True, "status_code": HTTPStatus.OK},
        },
        "degraded_services": [],
        "overall_healthy": True,
    }

    _map_get_supabase_service_health(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_supabase_service_health",
            "label": "Supabase Service Health",
            "summary": "3 services checked, all healthy",
            "url": None,
            "snippet": None,
        }
    ]


def test_service_health_mapper_names_the_degraded_services() -> None:
    evidence: dict[str, object] = {}
    output = {
        "services": {
            "postgrest": {"healthy": True},
            "auth": {"healthy": False, "status_code": HTTPStatus.SERVICE_UNAVAILABLE},
            "storage": {"healthy": False, "error": "timeout"},
        },
        "degraded_services": ["auth", "storage"],
        "overall_healthy": False,
    }

    _map_get_supabase_service_health(evidence, output, {})

    entries = evidence["catalog_entries"]
    assert isinstance(entries, list)
    assert entries[0]["summary"] == "3 services checked, degraded: auth, storage"


def test_service_health_mapper_records_nothing_without_services() -> None:
    evidence: dict[str, object] = {}

    _map_get_supabase_service_health(evidence, {"available": False}, {})

    assert "catalog_entries" not in evidence


def test_storage_buckets_mapper_records_entry() -> None:
    evidence: dict[str, object] = {}
    output = {
        "total_buckets": 2,
        "returned_buckets": 2,
        "truncated": False,
        "buckets": [
            {"id": "a", "name": "avatars", "public": True},
            {"id": "b", "name": "backups", "public": False},
        ],
    }

    _map_get_supabase_storage_buckets(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_supabase_storage_buckets",
            "label": "Supabase Storage Buckets",
            "summary": "2 buckets, public: avatars",
            "url": None,
            "snippet": None,
        }
    ]


def test_storage_buckets_mapper_notes_truncation() -> None:
    evidence: dict[str, object] = {}
    output = {
        "total_buckets": 10,
        "returned_buckets": 1,
        "truncated": True,
        "buckets": [{"id": "a", "name": "avatars", "public": False}],
    }

    _map_get_supabase_storage_buckets(evidence, output, {})

    entries = evidence["catalog_entries"]
    assert isinstance(entries, list)
    assert entries[0]["summary"] == "1 buckets (of 10)"


def test_storage_buckets_mapper_records_nothing_without_buckets() -> None:
    evidence: dict[str, object] = {}

    _map_get_supabase_storage_buckets(evidence, {"buckets": []}, {})

    assert "catalog_entries" not in evidence
