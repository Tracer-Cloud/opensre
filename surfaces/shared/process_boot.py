"""Process boot for the hosts ``surfaces`` starts: CLI, scheduler, shell, embedded.

:func:`configure_process` runs the shared step order from
:mod:`infrastructure.process.boot` with :data:`CAPABILITY_REGISTRARS`, the
registration ``tools`` and ``integrations`` own. ``infrastructure`` may import
neither package; this tier may import both, so it names them here.

``gateway`` may not import ``surfaces``, so :mod:`gateway.core.process.boot`
names the same registrars for the gateway and web hosts.
``tests/infrastructure/process/test_capability_registrars.py`` fails when the
two differ.
"""

from __future__ import annotations

import logging
from typing import Final

from infrastructure.process.boot import CapabilityRegistrars, ProcessProfile, boot_process
from integrations.harness_adapters import install_cli_auth_checker
from integrations.harness_adapters import (
    register_harness_adapters as register_integration_adapters,
)
from integrations.scheduled_delivery import install_scheduled_delivery_adapters
from tools.harness_adapters import register_harness_adapters as register_tool_adapters

CAPABILITY_REGISTRARS: Final = CapabilityRegistrars(
    integration_adapters=register_integration_adapters,
    tool_adapters=register_tool_adapters,
    cli_auth_checker=install_cli_auth_checker,
    scheduled_delivery=install_scheduled_delivery_adapters,
)


def configure_process(profile: ProcessProfile, *, logger: logging.Logger | None = None) -> None:
    """Boot this process for ``profile``. Idempotent per profile.

    Logging configuration is the caller's responsibility.
    """
    boot_process(profile, CAPABILITY_REGISTRARS, logger=logger)


__all__ = ["CAPABILITY_REGISTRARS", "configure_process"]
