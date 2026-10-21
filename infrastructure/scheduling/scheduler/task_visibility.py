"""Request-scoped visibility for tasks in the process-wide scheduler store."""

from __future__ import annotations

from config.constants.organization import organization_id
from config.principal import PrincipalKind
from config.scope_context import current_scope
from infrastructure.scheduling.scheduler.loop_owner import loop_is_private, owned_by
from infrastructure.scheduling.scheduler.types import ScheduledTask


def visible_to_current_scope(task: ScheduledTask, *, include_private: bool = False) -> bool:
    """Return whether the current turn may inspect or act on ``task``.

    A turn bound to an organization sees that organization's tasks, and a
    private loop only when its owner is acting. ``include_private`` also shows
    other members' private loops, for the web app sync alone: it mirrors the
    whole list and applies only changes the app already authorized.
    """
    scope = current_scope()
    if scope is None or scope.principal.kind != PrincipalKind.ORG:
        return True
    owner = task.organization or organization_id()
    if owner != scope.principal.id:
        return False
    return include_private or not loop_is_private(task) or owned_by(task, scope.actor.id)


__all__ = ["visible_to_current_scope"]
