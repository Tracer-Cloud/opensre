"""Install analytics entrypoint."""

from __future__ import annotations

from app.analytics.events import Event
from app.analytics.provider import (
    Properties,
    get_analytics,
    mark_install_detected,
    shutdown_analytics,
)

_INSTALL_PROPERTIES: Properties = {
    "install_source": "make_install",
    "entrypoint": "make install",
}


def main() -> int:
    mark_install_detected()
    get_analytics().capture(Event.INSTALL_DETECTED, _INSTALL_PROPERTIES)
    shutdown_analytics(flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
