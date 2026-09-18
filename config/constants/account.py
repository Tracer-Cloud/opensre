"""Personal OpenSRE account endpoints and local storage names."""

from __future__ import annotations

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
OPENSRE_ACCOUNT_USAGE_PATH = "/usage"
OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS = 15.0
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
    "OPENSRE_ACCOUNT_TOKEN_ENV",
    "OPENSRE_ACCOUNT_SESSION_PATH",
    "OPENSRE_ACCOUNT_USAGE_PATH",
    "OPENSRE_APP_URL_DEFAULT",
    "OPENSRE_APP_URL_DEV",
    "OPENSRE_APP_URL_ENV",
    "OPENSRE_GATEWAY_LLM_MODEL_DEFAULT",
    "OPENSRE_STAFF_EMAIL_DOMAIN",
]
