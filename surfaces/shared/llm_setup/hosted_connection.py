"""Account-hosted model selection without deleting local provider connections."""

from __future__ import annotations

import os

from config.account import load_account_record
from config.constants.account import (
    OPENSRE_ACCOUNT_LLM_MODEL_ENV,
    OPENSRE_ACCOUNT_TOKEN_ENV,
    OPENSRE_GATEWAY_LLM_MODEL_DEFAULT,
)
from config.constants.billing import WEBAPP_URL_ENV
from config.constants.llm import HOSTED_MODEL_PROVIDER, OPENSRE_LLM_CONNECTION_ENV
from config.env_file import sync_env_values
from surfaces.shared.llm_setup.auth_results import SelectionError, SelectionResult


def hosted_model() -> str | None:
    """Read only non-secret account metadata or injected environment hints."""
    record = load_account_record()
    if record is not None:
        return record.llm_model
    if os.getenv(OPENSRE_ACCOUNT_TOKEN_ENV) and os.getenv(WEBAPP_URL_ENV):
        return os.getenv(OPENSRE_ACCOUNT_LLM_MODEL_ENV) or OPENSRE_GATEWAY_LLM_MODEL_DEFAULT
    return None


def select_hosted(model: str | None) -> SelectionResult:
    """Restore the account route only after intentionally resolving its availability."""
    from config.account import resolve_account_token
    from core.llm.factory import reset_llm_clients

    available = hosted_model()
    if not available or not resolve_account_token():
        raise SelectionError(
            "Sign in with opensre account login before selecting hosted inference."
        )
    if model is not None and model != available:
        raise SelectionError("The hosted model is managed by your OpenSRE account.")
    path = sync_env_values({OPENSRE_LLM_CONNECTION_ENV: "account"})
    os.environ[OPENSRE_LLM_CONNECTION_ENV] = "account"
    reset_llm_clients()
    from surfaces.shared.llm_setup.handoff import notify_setup

    notify_setup(selected=True)
    return SelectionResult(HOSTED_MODEL_PROVIDER, available, path)
