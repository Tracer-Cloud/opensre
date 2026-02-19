"""Service map configuration."""

# Overhead resolved by async writes (daemon thread) — safe to enable.
SERVICE_MAP_ENABLED = True

# Write service_map.json in a background thread so it doesn't block publish.
SERVICE_MAP_WRITE_ASYNC = True


def is_service_map_enabled() -> bool:
    return SERVICE_MAP_ENABLED


def is_async_write_enabled() -> bool:
    return SERVICE_MAP_WRITE_ASYNC
