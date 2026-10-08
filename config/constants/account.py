"""Personal OpenSRE account endpoints and local storage names."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

OPENSRE_ACCOUNT_FILENAME = "account.json"
OPENSRE_ACCOUNT_METADATA_PATH_ENV = "OPENSRE_ACCOUNT_METADATA_PATH"
OPENSRE_ACCOUNT_TOKEN_ENV = "OPENSRE_ACCOUNT_TOKEN"
OPENSRE_ACCOUNT_LLM_BASE_PATH = "/api/llm/v1"
# A hosted gateway never logs in, so it cannot learn the served model the way a
# CLI does. The webapp enforces its own model on every request; this name only
# has to pick the matching OpenAI endpoint (gpt-5.6* → Responses API) and size
# the context window. Override it when the webapp's model changes family.
OPENSRE_ACCOUNT_LLM_MODEL_ENV = "OPENSRE_ACCOUNT_LLM_MODEL"
OPENSRE_GATEWAY_LLM_MODEL_DEFAULT = "gpt-5.6-sol"
OPENSRE_ACCOUNT_LOGIN_PATH = "/cli/auth/start"
OPENSRE_ACCOUNT_LOGIN_SUCCESS_PATH = "/cli/auth/success"
OPENSRE_ACCOUNT_EXCHANGE_PATH = "/api/auth/cli/exchange"
OPENSRE_ACCOUNT_SESSION_PATH = "/api/auth/cli/session"
OPENSRE_ACCOUNT_CREDITS_PATH = "/api/credits/balance"
OPENSRE_ACCOUNT_USAGE_PATH = "/usage"
#: Connections authorized for the signed-in user and active organization.
OPENSRE_ACCOUNT_INTEGRATIONS_PATH = "/api/auth/cli/integrations"
#: Query asking that route for the caller's personal connections too, each
#: record tagged with its ``owner``. Older apps ignore it.
OPENSRE_ACCOUNT_INTEGRATIONS_PERSONAL_PARAMS: Mapping[str, str] = MappingProxyType(
    {"include": "personal"}
)
#: The app's own service id -> the CLI integration it is. The app stores its
#: Slack OAuth install as ``slack_bot`` (bot token only; events reach the
#: hosted gateway over HTTP), which is the CLI's ``slack`` integration.
OPENSRE_ACCOUNT_SERVICE_NAMES: Mapping[str, str] = MappingProxyType({"slack_bot": "slack"})
#: Store-instance tags carrying a hosted connection's owner (``user`` or
#: ``organization`` plus its Clerk id) and whether it is that owner's default.
INTEGRATION_OWNER_KIND_TAG = "owner_kind"
INTEGRATION_OWNER_ID_TAG = "owner_id"
INTEGRATION_IS_DEFAULT_TAG = "is_default"
#: How long one fetched remote-integration snapshot stays fresh in-process.
OPENSRE_ACCOUNT_INTEGRATIONS_TTL_SECONDS = 60.0
#: Short fetch timeout so an offline laptop never stalls a turn on this call.
OPENSRE_ACCOUNT_INTEGRATIONS_TIMEOUT_SECONDS = 5.0
OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS = 15.0
#: Pauses before re-checking a login the app could not answer for (timeout, 429, 5xx).
OPENSRE_ACCOUNT_SESSION_RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.5, 2.0)
#: Most time the session check and its retries may take before the app counts as unreachable.
OPENSRE_ACCOUNT_SESSION_RETRY_BUDGET_SECONDS = 20.0
OPENSRE_APP_URL_DEFAULT = "https://app.opensre.com"
OPENSRE_APP_URL_DEV = "http://localhost:3000"
OPENSRE_APP_URL_ENV = "OPENSRE_APP_URL"
#: Accounts on this domain are OpenSRE staff; their email may identify them in telemetry.
OPENSRE_STAFF_EMAIL_DOMAIN = "@opensre.com"

__all__ = [
    "OPENSRE_ACCOUNT_FILENAME",
    "OPENSRE_ACCOUNT_METADATA_PATH_ENV",
    "OPENSRE_ACCOUNT_LLM_BASE_PATH",
    "OPENSRE_ACCOUNT_LLM_MODEL_ENV",
    "OPENSRE_ACCOUNT_LOGIN_PATH",
    "OPENSRE_ACCOUNT_LOGIN_SUCCESS_PATH",
    "OPENSRE_ACCOUNT_EXCHANGE_PATH",
    "OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS",
    "OPENSRE_ACCOUNT_INTEGRATIONS_PATH",
    "OPENSRE_ACCOUNT_INTEGRATIONS_TIMEOUT_SECONDS",
    "OPENSRE_ACCOUNT_INTEGRATIONS_TTL_SECONDS",
    "OPENSRE_ACCOUNT_SERVICE_NAMES",
    "OPENSRE_ACCOUNT_TOKEN_ENV",
    "OPENSRE_ACCOUNT_SESSION_PATH",
    "OPENSRE_ACCOUNT_SESSION_RETRY_BUDGET_SECONDS",
    "OPENSRE_ACCOUNT_SESSION_RETRY_DELAYS_SECONDS",
    "OPENSRE_ACCOUNT_CREDITS_PATH",
    "OPENSRE_ACCOUNT_USAGE_PATH",
    "OPENSRE_APP_URL_DEFAULT",
    "OPENSRE_APP_URL_DEV",
    "OPENSRE_APP_URL_ENV",
    "OPENSRE_GATEWAY_LLM_MODEL_DEFAULT",
    "OPENSRE_STAFF_EMAIL_DOMAIN",
]
