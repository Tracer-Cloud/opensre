"""Non-secret parent/child receipt for a deliberate login and model selection."""

from __future__ import annotations

import json
import os
from pathlib import Path

from config.constants.llm import OPENSRE_LLM_SETUP_RESULT_PATH_ENV


def report_setup(provider: str, *, activated: bool, credential_replaced: bool = False) -> None:
    target = os.getenv(OPENSRE_LLM_SETUP_RESULT_PATH_ENV)
    if target:
        Path(target).write_text(
            json.dumps(
                {
                    "provider": provider,
                    "activated": activated,
                    "credential_replaced": credential_replaced,
                }
            ),
            encoding="utf-8",
        )


def refresh_setup_result(path: Path) -> None:
    """Import only settings and deliberately replaced credentials from this login."""
    from config.env_file import read_env_lines
    from config.local_env import get_project_env_path
    from config.secrets.store import resolve_stored_secret
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
    from surfaces.shared.llm_setup.env_sync import (
        _env_value_from_lines,
        reload_provider_selection,
    )

    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(result, dict):
        return
    provider = PROVIDER_BY_VALUE.get(result.get("provider", ""))
    if provider is None:
        return
    if result.get("activated") is True:
        reload_provider_selection()
    else:
        lines = read_env_lines(get_project_env_path())
        for key in (
            provider.endpoint_env,
            provider.api_version_env,
            provider.api_key_env if not provider.credential_secret else "",
        ):
            if key and (value := _env_value_from_lines(lines, key)) is not None:
                os.environ[key] = value
    if (
        result.get("credential_replaced") is True
        and provider.credential_secret
        and provider.api_key_env
    ):
        key = resolve_stored_secret(provider.api_key_env)
        if key:
            os.environ[provider.api_key_env] = key
    from core.llm.factory import reset_llm_clients

    reset_llm_clients()
