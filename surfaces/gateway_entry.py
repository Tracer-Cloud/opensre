"""Process composition root for the messaging gateway.

``surfaces`` and ``gateway`` are peer packages: gateway must not import
surfaces. This module owns the glue — headless slash ports from the
interactive shell passed into :class:`gateway.core.lifecycle.controller.GatewayController`.

Started by the daemon as ``python -m surfaces.gateway_entry`` (also
``opensre gateway start`` / ``opensre gateway start --foreground``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from surfaces.interactive_shell.runtime.slash_adapter import (
    SlashPorts,
    headless_slash_ports,
)

if TYPE_CHECKING:
    from gateway.core.lifecycle.controller import GatewayController


def gateway_slash_ports_factory() -> SlashPorts:
    """Build slash runtime ports for non-interactive gateway turns."""
    return headless_slash_ports()


def serve_web() -> None:
    """Serve the health and alert HTTP app in the foreground (container ``MODE=web``)."""
    from gateway.web.web_server import serve_webapp_foreground

    serve_webapp_foreground()


def start_gateway(*, wait: bool = True) -> GatewayController:
    """Start the gateway with headless slash ports injected for chat turns."""
    from config.local_env import bootstrap_opensre_env_once
    from gateway.core.lifecycle.controller import GatewayController

    bootstrap_opensre_env_once(override=False)
    return GatewayController(
        slash_ports_factory=gateway_slash_ports_factory,
    ).start_gateway(wait=wait)


def main() -> None:
    start_gateway()


__all__ = [
    "gateway_slash_ports_factory",
    "main",
    "serve_web",
    "start_gateway",
]


if __name__ == "__main__":
    main()
