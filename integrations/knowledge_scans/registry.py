"""The integrations that can be scanned into workspace knowledge.

Add a scanner here when an integration gains a ``knowledge_scan`` module; the
service schedules every registered one the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.domain.knowledge import ORGANIZATION_SLUG, ScanOutcome, Summarize, integration_slug


@dataclass(frozen=True)
class Scanner:
    """One integration's scan: whether it can run here, what it writes, and the scan itself."""

    service: str
    available: Callable[[], bool]
    scan: Callable[[Summarize | None], ScanOutcome]
    #: Every workspace entry the scan writes; one missing or old makes it due.
    writes: tuple[str, ...]
    #: Refreshes who may read workspace knowledge, more often than a full scan
    #: (a member list costs one API call, a scan reads channels and calls a model).
    refresh_readers: Callable[[], bool] | None = None


def _slack_available() -> bool:
    from integrations.slack.web_client import bot_token_configured

    return bot_token_configured()


def _slack_scan(summarize: Summarize | None) -> ScanOutcome:
    from integrations.slack.knowledge_scan import scan_slack_workspace

    return scan_slack_workspace(summarize=summarize)


def _slack_readers() -> bool:
    from integrations.slack.knowledge_scan import refresh_slack_readers

    return refresh_slack_readers()


SCANNERS: tuple[Scanner, ...] = (
    Scanner(
        "slack",
        available=_slack_available,
        scan=_slack_scan,
        writes=(ORGANIZATION_SLUG, integration_slug("slack")),
        refresh_readers=_slack_readers,
    ),
)

#: Every service a scan exists for.
SCANNABLE_SERVICES: tuple[str, ...] = tuple(scanner.service for scanner in SCANNERS)

__all__ = ["SCANNABLE_SERVICES", "SCANNERS", "Scanner"]
