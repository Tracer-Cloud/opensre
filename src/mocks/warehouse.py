"""Mock Warehouse and Loader client for the demo."""

from datetime import datetime, timezone
from typing import Optional
from fixtures.mock_data import WAREHOUSE_TABLES, LOADER_STATUS


class MockWarehouseClient:
    """Mock Warehouse API client that returns predefined data for the demo scenario."""

    def __init__(self):
        self._tables = WAREHOUSE_TABLES
        self._loaders = LOADER_STATUS

    def get_table_freshness(self, table_name: str) -> Optional[dict]:
        """
        Get freshness information for a table.
        
        Returns:
            Table info including last_updated, expected interval, and staleness status
        """
        table = self._tables.get(table_name)
        if table is None:
            return None
        
        now = datetime(2026, 1, 13, 2, 15, 0, tzinfo=timezone.utc)  # Demo time
        hours_since_update = (now - table["last_updated"]).total_seconds() / 3600
        is_stale = hours_since_update > table["expected_update_interval_hours"]
        
        return {
            "table_name": table["table_name"],
            "schema": table["schema"],
            "last_updated": table["last_updated"].isoformat(),
            "row_count": table["row_count"],
            "expected_update_interval_hours": table["expected_update_interval_hours"],
            "hours_since_update": round(hours_since_update, 2),
            "is_stale": is_stale,
        }

    def get_loader_status(self, loader_name: str) -> Optional[dict]:
        """
        Get status of a data loader (Service B).
        
        Returns:
            Loader status including what it's waiting for
        """
        loader = self._loaders.get(loader_name)
        if loader is None:
            return None
        
        return {
            "loader_name": loader["loader_name"],
            "status": loader["status"],
            "target_table": loader["target_table"],
            "waiting_for": loader["waiting_for"],
            "last_check": loader["last_check"].isoformat(),
            "checks_since_last_success": loader["checks_since_last_success"],
        }

    def list_loaders_for_table(self, table_name: str) -> list[dict]:
        """Get all loaders that target a specific table."""
        loaders = []
        for loader in self._loaders.values():
            if loader["target_table"] == table_name:
                loaders.append(self.get_loader_status(loader["loader_name"]))
        return loaders


# Singleton instance for the demo
_warehouse_client: Optional[MockWarehouseClient] = None


def get_warehouse_client() -> MockWarehouseClient:
    """Get the mock warehouse client singleton."""
    global _warehouse_client
    if _warehouse_client is None:
        _warehouse_client = MockWarehouseClient()
    return _warehouse_client

