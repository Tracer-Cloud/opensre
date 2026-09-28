"""Capability names a host records on a session for tools to read."""

#: Set by a long-lived host whose in-process scheduler picks up tasks added to the
#: store, so a scheduling tool need not install an OS-level service.
SCHEDULER_HOST_CAPABILITY = "scheduler_host"
SCHEDULER_HOST_IN_PROCESS = "in_process"

__all__ = ["SCHEDULER_HOST_CAPABILITY", "SCHEDULER_HOST_IN_PROCESS"]
