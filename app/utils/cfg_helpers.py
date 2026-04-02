"""Configuration helper utilities."""

import os


class CfgHelpers:
    """Static class for config helper methods."""

    @staticmethod
    def first_env_or_default(env_keys: tuple[str, ...], default: str) -> str:
        """First non-empty environment value among *env_keys*, else *default*.

        Args:
            env_keys (tuple[str, ...]): The tuple of environment variable keys.
            default (str): The default value if no environment variable is found.

        Returns:
            str: The first non-empty environment value.
        """
        for key in env_keys:
            value = CfgHelpers.get_clean_env_value(key)
            if value:
                return value
        return default

    @staticmethod
    def resolve_llm_provider() -> str:
        """Resolve the LLM provider from env var.
        Returns:
            str: The provider string.
        """
        return (os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()

    @staticmethod
    def get_clean_env_value(env_key: str) -> str:
        """Get a clean environment value from the env var.
        Args:
            env_key (str): The environment variable key.
        Returns:
            str: The stripped environment value.
        """
        return (os.getenv(env_key, "")).strip()
