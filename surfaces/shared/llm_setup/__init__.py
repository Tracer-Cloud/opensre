"""LLM provider setup: the provider catalog, credential validation, auth and .env sync shared by the CLI and the shell."""

from surfaces.shared.llm_setup.auth_profiles import (
    ProviderAuthProfile,
    iter_auth_profiles,
    resolve_auth_profile,
)
from surfaces.shared.llm_setup.auth_results import AuthStatus
from surfaces.shared.llm_setup.auth_service import (
    AuthSetupError,
    configure_api_key_provider,
    logout_provider,
    provider_status,
)

__all__ = [
    "AuthSetupError",
    "AuthStatus",
    "ProviderAuthProfile",
    "configure_api_key_provider",
    "iter_auth_profiles",
    "logout_provider",
    "provider_status",
    "resolve_auth_profile",
]
