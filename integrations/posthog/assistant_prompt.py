"""PostHog conversational-assistant prompt fragment.

Registered with :func:`infrastructure.harness_providers.register_assistant_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations

from core.agent_harness.spi.prompt_chrome import COHORT_IDENTITY_UNVERIFIED_MARK


def posthog_assistant_prompt_fragment() -> str:
    return (
        "PostHog metric report: open **I found:** with a one-line scope summary "
        "(window, project, metric count). Present a per-metric table with columns "
        "Metric | Current | Previous | Change | % | Trend. Use up/down/flat words "
        "for the trend, never colour alone. After the table, call out the 1-3 most "
        "notable movers with a short why-it-matters line and one clear next action "
        "when the data supports it. When a metric window returns no data, say so "
        "explicitly for that metric; distinguish real zeros from failed queries — "
        "never silently widen the window or present a failed query as zero. "
        "Signup / signed-up / retention-of-signups: never treat user_signed_in / "
        "login events as signup. If the signup event is unverified, say so with "
        f"'{COHORT_IDENTITY_UNVERIFIED_MARK}' and draft HogQL that leaves the event "
        "name as a placeholder — do not publish a retention % or signup count."
    )


__all__ = ["posthog_assistant_prompt_fragment"]
