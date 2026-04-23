from __future__ import annotations

from app.integrations.azure_sql import DEFAULT_AZURE_SQL_PORT
from app.nodes.plan_actions.detect_sources import detect_sources


def test_detect_sources_includes_azure_sql_from_resolved_integrations() -> None:
    sources = detect_sources(
        raw_alert={"annotations": {}},
        context={},
        resolved_integrations={
            "azure_sql": {
                "server": "prod.db.windows.net",
                "database": "orders",
                "port": DEFAULT_AZURE_SQL_PORT,
                "username": "svc-user",
                "password": "secret",
            }
        },
    )

    assert sources["azure_sql"] == {
        "server": "prod.db.windows.net",
        "database": "orders",
        "port": DEFAULT_AZURE_SQL_PORT,
        "connection_verified": True,
    }


def test_detect_sources_azure_sql_uses_annotation_database_override() -> None:
    sources = detect_sources(
        raw_alert={"commonAnnotations": {"azure_sql_database": "tenant_a"}},
        context={},
        resolved_integrations={
            "azure_sql": {
                "server": "prod.db.windows.net",
                "database": "orders",
                "port": DEFAULT_AZURE_SQL_PORT,
            }
        },
    )

    assert sources["azure_sql"]["database"] == "tenant_a"


def test_detect_sources_azure_sql_uses_generic_database_annotation_fallback() -> None:
    """Tier-2 fallback: a generic `database` annotation (with no
    `azure_sql_database` key) should override the stored database, matching
    the behaviour of the mysql/postgresql branches."""
    sources = detect_sources(
        raw_alert={"annotations": {"database": "tenant_b"}},
        context={},
        resolved_integrations={
            "azure_sql": {
                "server": "prod.db.windows.net",
                "database": "orders",
                "port": DEFAULT_AZURE_SQL_PORT,
            }
        },
    )

    assert sources["azure_sql"]["database"] == "tenant_b"


def test_detect_sources_azure_sql_ignores_blank_annotation_override() -> None:
    sources = detect_sources(
        raw_alert={"commonAnnotations": {"azure_sql_database": "   "}},
        context={},
        resolved_integrations={
            "azure_sql": {
                "server": "prod.db.windows.net",
                "database": "orders",
                "port": DEFAULT_AZURE_SQL_PORT,
            }
        },
    )

    assert sources["azure_sql"]["database"] == "orders"


def test_detect_sources_azure_sql_defaults_port_when_missing_or_falsy() -> None:
    sources_with_none = detect_sources(
        raw_alert={"annotations": {}},
        context={},
        resolved_integrations={
            "azure_sql": {
                "server": "prod.db.windows.net",
                "database": "orders",
                "port": None,
            }
        },
    )
    sources_with_zero = detect_sources(
        raw_alert={"annotations": {}},
        context={},
        resolved_integrations={
            "azure_sql": {
                "server": "prod.db.windows.net",
                "database": "orders",
                "port": 0,
            }
        },
    )

    assert sources_with_none["azure_sql"]["port"] == DEFAULT_AZURE_SQL_PORT
    assert sources_with_zero["azure_sql"]["port"] == DEFAULT_AZURE_SQL_PORT


def test_detect_sources_azure_sql_requires_server_and_database() -> None:
    missing_server = detect_sources(
        raw_alert={"annotations": {}},
        context={},
        resolved_integrations={"azure_sql": {"server": "", "database": "orders"}},
    )
    missing_database = detect_sources(
        raw_alert={"annotations": {}},
        context={},
        resolved_integrations={"azure_sql": {"server": "prod.db.windows.net", "database": ""}},
    )

    assert "azure_sql" not in missing_server
    assert "azure_sql" not in missing_database
