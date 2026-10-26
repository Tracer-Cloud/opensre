"""Regression coverage for shared MongoDB integration parameters."""

from __future__ import annotations

from unittest.mock import patch

from integrations.mongodb.tools.mongodb_current_ops_tool import get_mongodb_current_ops
from integrations.mongodb.tools.mongodb_replica_status_tool import get_mongodb_replica_status
from integrations.mongodb.tools.mongodb_server_status_tool import get_mongodb_server_status


def test_admin_tools_ignore_database_injected_by_shared_config() -> None:
    """Admin tools accept the database value injected for database-scoped tools."""
    shared_params = {
        "connection_string": "mongodb://localhost:27017",
        "database": "opensre_verify",
    }

    with (
        patch(
            "integrations.mongodb.tools.mongodb_current_ops_tool.get_current_ops",
            return_value={"available": True},
        ),
        patch(
            "integrations.mongodb.tools.mongodb_replica_status_tool.get_rs_status",
            return_value={"available": True},
        ),
        patch(
            "integrations.mongodb.tools.mongodb_server_status_tool.get_server_status",
            return_value={"available": True},
        ),
    ):
        assert get_mongodb_current_ops(**shared_params)["available"] is True
        assert get_mongodb_replica_status(**shared_params)["available"] is True
        assert get_mongodb_server_status(**shared_params)["available"] is True
