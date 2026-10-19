"""The integrations that can be scanned into workspace knowledge.

Add a scanner here when an integration gains a ``knowledge_scan`` module; the
service schedules every registered one the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.domain.knowledge import ScanOutcome, Summarize


@dataclass(frozen=True)
class Scanner:
    """One integration's scan: whether it can run here, and the scan itself."""

    service: str
    available: Callable[[], bool]
    scan: Callable[[Summarize | None], ScanOutcome]


def _slack_available() -> bool:
    from integrations.slack.web_client import bot_token_configured

    return bot_token_configured()


def _slack_scan(summarize: Summarize | None) -> ScanOutcome:
    from integrations.slack.knowledge_scan import scan_slack_workspace

    return scan_slack_workspace(summarize=summarize)


SCANNERS: tuple[Scanner, ...] = (Scanner("slack", _slack_available, _slack_scan),)

SCANNERS_BY_SERVICE: dict[str, Scanner] = {scanner.service: scanner for scanner in SCANNERS}

__all__ = ["SCANNERS", "SCANNERS_BY_SERVICE", "Scanner"]
