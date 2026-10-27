"""Provider-owned model access checks for explicit selection."""

from __future__ import annotations

from surfaces.shared.llm_setup.catalog import ProviderOption, WizardCredentialKind
from surfaces.shared.llm_setup.model_results import ModelSelectionError


def validate_model_access(provider: ProviderOption, model: str) -> None:
    if provider.credential_kind == WizardCredentialKind.SUBSCRIPTION:
        import httpx

        from core.llm.subscriptions.registry import subscription_adapter
        from core.llm.subscriptions.types import SubscriptionLoginRequired

        try:
            models = subscription_adapter(provider.value).auth.verify()
        except SubscriptionLoginRequired as exc:
            raise ModelSelectionError(str(exc)) from exc
        except (httpx.HTTPError, RuntimeError, ValueError, OSError) as exc:
            raise ModelSelectionError(
                f"Could not verify {provider.label} model access. Run opensre auth verify {provider.value} or opensre auth login {provider.value}, then retry."
            ) from exc
        if model not in {value for value, _ in models}:
            raise ModelSelectionError(
                f"Choose a model available to this {provider.label} account. Run opensre auth login {provider.value}."
            )
    if provider.credential_kind == WizardCredentialKind.CLI and provider.adapter_factory:
        probe = provider.adapter_factory().detect()
        if not probe.installed or probe.logged_in is False:
            raise ModelSelectionError(f"{probe.detail} Run opensre auth login {provider.value}.")
    from surfaces.shared.llm_setup.discovery import validate_discovered_model

    validate_discovered_model(provider, model)
