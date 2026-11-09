"""The gateway process: control it from outside, report from inside.

This package's ``__init__`` is the facade other tiers import. A surface drives
the process — start, stop, ask whether it is running, read what it reports —
and gets those names from here rather than reaching at the modules behind it.

Inside the running process, :mod:`gateway.core.process.component_status`
publishes state and :mod:`gateway.core.process.readiness` flips the ready flag;
those are the gateway's own business and are imported directly.
"""

from gateway.core.process.component_status import read_component_status
from gateway.core.process.supervision import (
    GATEWAY_LOG_FILE,
    gateway_daemon_pid,
    read_gateway_log_tail,
    start_gateway_daemon,
    stop_gateway_daemon,
)

__all__ = [
    "GATEWAY_LOG_FILE",
    "gateway_daemon_pid",
    "read_component_status",
    "read_gateway_log_tail",
    "start_gateway_daemon",
    "stop_gateway_daemon",
]
