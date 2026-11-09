"""Provider-aware API key resolver for LLM clients."""

from __future__ import annotations


def resolve_llm_api_key(env_name: str) -> str:
    """Return the API key for *env_name* or raise with a clear provider hint."""
    from config.llm_auth.credentials import resolve_api_key_env_for_request
    from config.llm_auth.provider_catalog import API_KEY_PROVIDER_ENVS

    resolved = resolve_api_key_env_for_request(env_name)
    if resolved:
        return resolved
    for provider, provider_env in API_KEY_PROVIDER_ENVS.items():
        if provider_env == env_name:
            raise RuntimeError(
                f"Missing credential for LLM provider '{provider}'. Set {env_name} "
                f"or run `opensre login {provider}`."
            )
    return resolved
