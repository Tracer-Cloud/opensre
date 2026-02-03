"""Service map builder - tracks discovered assets and edges across investigations."""

from .builder import build_service_map
from .storage import load_service_map, persist_service_map
from .summary import get_compact_asset_inventory
from .types import Asset, Edge, HistoryEntry, ServiceMap

__all__ = [
    "Asset",
    "Edge",
    "HistoryEntry",
    "ServiceMap",
    "build_service_map",
    "load_service_map",
    "persist_service_map",
    "get_compact_asset_inventory",
]
