from __future__ import annotations

import os

import pytest

from app.integrations.posthog import (
    BounceRateAlert,
    check_bounce_rate_alert,
    posthog_config_from_env,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTHOG_PERSONAL_API_KEY") or not os.getenv("POSTHOG_PROJECT_ID"),
    reason="PostHog env vars not set — skipping E2E",
)


def test_posthog_bounce_rate_e2e() -> None:
    """E2E: bounce rate alert flow works end-to-end."""

    config = posthog_config_from_env()
    assert config is not None, "PostHog config should be loaded from env"

    alert = check_bounce_rate_alert(config)

    if alert is not None:
        assert isinstance(alert, BounceRateAlert)
        assert 0.0 <= alert.bounce_rate <= 1.0
        assert isinstance(alert.message, str)

