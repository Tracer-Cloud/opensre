"""Service map persistence."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from app.agent.memory.io import get_memories_dir

from .config import is_service_map_enabled
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


def persist_service_map(service_map: ServiceMap) -> Path:
    """Persist service map to disk (overwrite)."""
    # Ensure history is capped at 20 entries before persisting
    if len(service_map.get("history", [])) > 20:
        service_map["history"] = service_map["history"][-20:]

    service_map_path = get_memories_dir() / "service_map.json"
    service_map_path.parent.mkdir(parents=True, exist_ok=True)

    with service_map_path.open("w") as f:
        json.dump(service_map, f, indent=2)

    return service_map_path
