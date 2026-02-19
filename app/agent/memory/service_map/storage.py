"""Service map persistence."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from app.agent.memory.io import get_memories_dir

from .config import is_async_write_enabled, is_service_map_enabled
from .types import ServiceMap


def _empty_map() -> ServiceMap:
    return {
        "enabled": is_service_map_enabled(),
        "last_updated": datetime.now(UTC).isoformat(),
        "assets": [],
        "edges": [],
        "history": [],
    }


def load_service_map() -> ServiceMap:
    """Load existing service map from disk."""
    service_map_path = get_memories_dir() / "service_map.json"
    if not service_map_path.exists():
        return _empty_map()

    try:
        with service_map_path.open("r") as f:
            return cast(ServiceMap, json.load(f))
    except (json.JSONDecodeError, OSError):
        return _empty_map()


def _write_service_map_sync(service_map: ServiceMap) -> Path:
    """Write service map to disk synchronously."""
    if len(service_map.get("history", [])) > 20:
        service_map["history"] = service_map["history"][-20:]

    service_map_path = get_memories_dir() / "service_map.json"
    service_map_path.parent.mkdir(parents=True, exist_ok=True)

    with service_map_path.open("w") as f:
        json.dump(service_map, f, indent=2)

    return service_map_path


def persist_service_map(service_map: ServiceMap) -> Path:
    """Persist service map to disk.

    When SERVICE_MAP_WRITE_ASYNC is True, the write runs in a daemon thread
    so it does not block the main investigation pipeline.
    """
    if is_async_write_enabled():
        t = threading.Thread(
            target=_write_service_map_sync,
            args=(service_map,),
            daemon=True,
        )
        t.start()
        # Return the expected path even though write is async
        return get_memories_dir() / "service_map.json"

    return _write_service_map_sync(service_map)
