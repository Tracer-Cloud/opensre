"""Request-scoped visibility for tasks in the process-wide scheduler store."""

from __future__ import annotations

from config.constants.organization import organization_id
from config.principal import PrincipalKind
from config.scope_context import current_scope
from infrastructure.scheduling.scheduler.types import ScheduledTask


def visible_to_current_scope(task: ScheduledTask) -> bool:
    """Return whether the current turn may inspect or act on ``task``."""
    scope = current_scope()
    if scope is None or scope.principal.kind != PrincipalKind.ORG:
        return True
    owner = task.organization or organization_id()
    return owner == scope.principal.id


__all__ = ["visible_to_current_scope"]
