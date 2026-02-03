"""Service map configuration (no env flags)."""

# Default OFF: Validated benchmarks show 16.5% overhead currently
# Turn ON when action-skipping and hotspot prioritization are implemented
SERVICE_MAP_ENABLED = False


def is_service_map_enabled() -> bool:
    return SERVICE_MAP_ENABLED
