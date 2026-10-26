"""Install analytics entrypoint."""

from __future__ import annotations

from config.constants.paths import get_store_path
from infrastructure.analytics.install_state import record_install_marker_state
from infrastructure.analytics.provider import (
    Properties,
    capture_install_detected_if_needed,
    shutdown_analytics,
)

_INSTALL_PROPERTIES: Properties = {
    "install_source": "make_install",
    "entrypoint": "make install",
}

# One-shot install exit has no interactive spinner to keep snappy, so it can wait
# a full send-timeout for the install_detected POST to land (a conversion signal).
_INSTALL_FLUSH_TIMEOUT_SECONDS = 2.0


def main() -> int:
    record_install_marker_state(get_store_path().parent)
    capture_install_detected_if_needed(_INSTALL_PROPERTIES)
    shutdown_analytics(flush=True, timeout=_INSTALL_FLUSH_TIMEOUT_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
