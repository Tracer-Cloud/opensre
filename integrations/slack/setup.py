"""What Slack needs before it is considered configured.

Slack accepts an incoming webhook URL *or* Socket Mode tokens (bot + app). A
picker chooses which of the two (or both) to configure; picking one mode clears
the other's fields — choose "Both" to run both at once. The either/or rule (and
the ``xoxb-``/``xapp-`` prefix checks) lives in
:func:`integrations.slack.verifier.verify_slack`, so setup and health checks
agree on what "configured" means.

The webhook URL embeds its own secret, so — like Rocket.Chat's webhook — it
stays store-only. Socket Mode tokens are mirrored to the keyring.
"""

from __future__ import annotations

from config.constants.slack import (
    SLACK_APP_TOKEN_ENV,
    SLACK_BOT_TOKEN_ENV,
    SLACK_DEFAULT_CHAT_ID_ENV,
)
from integrations.setup_flow import IntegrationSetupSpec, SetupField, SetupMode
from integrations.slack.verifier import verify_slack

WEBHOOK_URL_FIELD = "webhook_url"
BOT_TOKEN_FIELD = "bot_token"
APP_TOKEN_FIELD = "app_token"
DEFAULT_CHAT_ID_FIELD = "default_chat_id"


SLACK_SETUP = IntegrationSetupSpec(
    service="slack",
    fields=(
        SetupField(
            name=WEBHOOK_URL_FIELD,
            label="Slack webhook URL",
            prompt="Slack webhook URL",
            # Store-only: the URL embeds its secret.
            required=False,
            secret=True,
        ),
        SetupField(
            name=BOT_TOKEN_FIELD,
            label="Slack bot token",
            prompt="Slack bot token (xoxb-…)",
            env_var=SLACK_BOT_TOKEN_ENV,
            required=False,
            secret=True,
        ),
        SetupField(
            name=APP_TOKEN_FIELD,
            label="Slack app-level token",
            prompt="Slack app-level token (xapp-…)",
            env_var=SLACK_APP_TOKEN_ENV,
            required=False,
            secret=True,
        ),
        SetupField(
            name=DEFAULT_CHAT_ID_FIELD,
            label="Default Slack channel ID",
            prompt="Default Slack channel ID for scheduled delivery (C…)",
            env_var=SLACK_DEFAULT_CHAT_ID_ENV,
            required=False,
            secret=False,
        ),
    ),
    mode_prompt="Slack setup:",
    modes=(
        SetupMode(
            value="webhook",
            label="Incoming webhook (outbound delivery)",
            fields=(WEBHOOK_URL_FIELD,),
        ),
        SetupMode(
            value="socket",
            label="Socket Mode bot (two-way gateway chat)",
            fields=(BOT_TOKEN_FIELD, APP_TOKEN_FIELD, DEFAULT_CHAT_ID_FIELD),
        ),
        SetupMode(
            value="both",
            label="Both webhook and Socket Mode",
            fields=(WEBHOOK_URL_FIELD, BOT_TOKEN_FIELD, APP_TOKEN_FIELD, DEFAULT_CHAT_ID_FIELD),
        ),
    ),
    verify=verify_slack,
)

__all__ = [
    "APP_TOKEN_FIELD",
    "BOT_TOKEN_FIELD",
    "DEFAULT_CHAT_ID_FIELD",
    "SLACK_SETUP",
    "WEBHOOK_URL_FIELD",
]
