"""Each part of the codebase imports vendors through their main module.

Every ``integrations/<vendor>/`` folder should be used through its main module
``integrations.<vendor>``. Below is the list, per top-level folder, of vendor
files still imported directly from outside the vendor (plus any name imported
from a vendor's ``__init__`` that it does not list in ``__all__``). The list can
only get shorter: adding a new direct import fails the test, and an entry that is
no longer imported must be deleted from the list.

``core`` and ``config`` already import nothing internal. Imports between vendors
(one vendor reaching inside another) are handled separately and are not checked
here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.shared.integrations_api import INTEGRATIONS_BORDER

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Vendor files each top-level folder still imports directly, instead of through
#: the vendor's main module ``integrations.<vendor>``.
_ALLOWED: dict[str, frozenset[str]] = {
    "core": frozenset(),
    "config": frozenset(),
    "infrastructure": frozenset({}),
    "gateway": frozenset(
        {
            "integrations.telegram.delivery",
            "integrations.telegram.formatting",
        }
    ),
    "bootstrap": frozenset(
        {
            "integrations.discord.scheduled_delivery",
            "integrations.rocketchat.scheduled_delivery",
            "integrations.slack.scheduled_delivery",
            "integrations.telegram.scheduled_delivery",
        }
    ),
    "tools": frozenset(
        {
            "integrations.sentry.issue_url",
        }
    ),
    "surfaces": frozenset(
        {
            "integrations.betterstack.setup",
            "integrations.dagster.setup",
            "integrations.jenkins.setup",
            "integrations.posthog.report_prerequisites",
            "integrations.posthog.setup",
            "integrations.posthog_mcp.setup",
            "integrations.sentry.digest_prerequisites",
            "integrations.sentry.setup",
            "integrations.sentry.uptime",
            "integrations.sentry_mcp.setup",
            "integrations.telegram.setup",
            "integrations.tempo.setup",
            "integrations.tracer.integrations_adapter",
        }
    ),
}


@pytest.mark.parametrize("consumer", sorted(_ALLOWED))
def test_consumer_vendor_imports_match_the_allowlist(consumer: str) -> None:
    # Arrange / Act
    imported = INTEGRATIONS_BORDER.internal_imports_under(
        REPO_ROOT / consumer, exclude_parts=frozenset({"tests"})
    )

    # Assert
    INTEGRATIONS_BORDER.assert_matches_allowlist(
        imported, _ALLOWED[consumer], consumer=f"{consumer}/"
    )
