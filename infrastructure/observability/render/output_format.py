"""Output format probe — what shape should rendered output take.

Pure env/TTY check. No injection needed — every caller wants the same
answer for a given process. Returns one of :data:`OUTPUT_FORMAT_RICH`,
:data:`OUTPUT_FORMAT_TEXT`, or :data:`OUTPUT_FORMAT_NONE` (callers
typically compare against the named constant rather than the bare
string).

Caller is responsible for honoring the verdict.
"""

from __future__ import annotations

import os
import sys

from config.constants.slack import SLACK_WEBHOOK_URL_ENV

# Output-format return values. Use these constants in callers instead
# of the bare strings so a future rename happens in one place.
OUTPUT_FORMAT_RICH = "rich"
OUTPUT_FORMAT_TEXT = "text"
OUTPUT_FORMAT_NONE = "none"

# Env-var keys the probe consults, in priority order. Named constants keep the
# spelling in one place and make ``grep`` for "who reads TRACER_OUTPUT_FORMAT"
# trivial.
_ENV_OUTPUT_FORMAT = "TRACER_OUTPUT_FORMAT"
_ENV_NO_COLOR = "NO_COLOR"


def get_output_format() -> str:
    """Return one of the ``OUTPUT_FORMAT_*`` constants based on env + TTY.

    Priority order:
    1. ``TRACER_OUTPUT_FORMAT`` env var — explicit override wins.
    2. ``NO_COLOR`` set (any value) — force text.
    3. ``SLACK_WEBHOOK_URL`` set — force text (Slack-bound output).
    4. Default: rich if stdout is a TTY, text otherwise.
    """
    if fmt := os.getenv(_ENV_OUTPUT_FORMAT):
        return fmt
    if os.getenv(_ENV_NO_COLOR) is not None:
        return OUTPUT_FORMAT_TEXT
    if os.getenv(SLACK_WEBHOOK_URL_ENV):
        return OUTPUT_FORMAT_TEXT
    return OUTPUT_FORMAT_RICH if sys.stdout.isatty() else OUTPUT_FORMAT_TEXT
