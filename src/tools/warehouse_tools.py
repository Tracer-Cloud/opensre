"""
Warehouse and Loader tools for the investigation agent.

Code #4: Context connectors
Functions that fetch context from the data warehouse and loader services.
"""

from langchain_core.tools import tool

from src.mocks.warehouse import get_warehouse_client


@tool
def get_table_freshness(table_name: str) -> dict:
    """
    Get freshness information for a warehouse table.
    
    Use this to:
    - Check when a table was last updated
    - Determine if a table is stale (breached SLA)
    - Get the expected update interval
    
    Args:
        table_name: The table name (e.g., 'events_fact')
    
    Returns:
        Table freshness info including staleness status
    """
    client = get_warehouse_client()
    info = client.get_table_freshness(table_name)
    
    if info is None:
        return {
            "table_name": table_name,
            "found": False,
            "message": f"Table '{table_name}' not found"
        }
    
    return {
        "table_name": table_name,
        "found": True,
        "freshness": info,
        "message": (
            f"Table '{table_name}' last updated {info['hours_since_update']:.1f} hours ago. "
            f"{'STALE' if info['is_stale'] else 'Fresh'} "
            f"(expected interval: {info['expected_update_interval_hours']}h)"
        )
    }


@tool
def get_loader_status(table_name: str) -> dict:
    """
    Get the status of data loaders targeting a specific table.
    
    Use this to:
    - Check if a loader is running or waiting
    - Find out what the loader is waiting for
    - Identify loader-related issues
    
    Args:
        table_name: The target table name (e.g., 'events_fact')
    
    Returns:
        Loader status information
    """
    client = get_warehouse_client()
    loaders = client.list_loaders_for_table(table_name)
    
    if not loaders:
        return {
            "table_name": table_name,
            "found": False,
            "loaders": [],
            "message": f"No loaders found targeting table '{table_name}'"
        }
    
    # Check if any loaders are waiting
    waiting = [l for l in loaders if l["status"] == "WAITING"]
    running = [l for l in loaders if l["status"] == "RUNNING"]
    
    return {
        "table_name": table_name,
        "found": True,
        "loaders": loaders,
        "summary": {
            "total": len(loaders),
            "waiting": len(waiting),
            "running": len(running),
        },
        "waiting_details": [
            {"name": l["loader_name"], "waiting_for": l["waiting_for"]} 
            for l in waiting
        ],
        "message": (
            f"Found {len(loaders)} loader(s) for table '{table_name}': "
            f"{len(waiting)} waiting, {len(running)} running"
        )
    }

