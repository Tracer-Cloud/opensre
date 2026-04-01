"""Registry of all available investigation actions."""

import logging

from app.tools.tool_actions.base import BaseTool

logger = logging.getLogger(__name__)


def get_available_actions() -> list[BaseTool]:
    """Return all registered investigation tools.

    Each integration package exposes a ``TOOLS`` list of ``BaseTool`` instances.
    Adding a new tool requires only creating a ``BaseTool`` subclass and appending
    its instance to the relevant package's ``TOOLS`` list — no edits needed here.
    """
    from app.tools.tool_actions.aws import TOOLS as aws
    from app.tools.tool_actions.datadog import TOOLS as dd
    from app.tools.tool_actions.github import TOOLS as github
    from app.tools.tool_actions.grafana import TOOLS as grafana
    from app.tools.tool_actions.knowledge_sre_book import TOOLS as knowledge
    from app.tools.tool_actions.sentry import TOOLS as sentry
    from app.tools.tool_actions.tracer import TOOLS as tracer

    try:
        from app.tools.tool_actions.eks import TOOLS as eks
    except ModuleNotFoundError as exc:
        logger.warning("[actions] EKS actions unavailable: %s", exc)
        eks = []

    return [*dd, *grafana, *aws, *tracer, *github, *sentry, *knowledge, *eks]
