"""Azure OpenAI wizard helpers: endpoint setup, deployment picker, validation."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from core.llm.providers.azure_openai import (
    discover_azure_openai_deployments_from_env,
    is_azure_openai_provider,
    normalize_azure_openai_base_url,
    resolve_azure_openai_api_version,
)
from infrastructure.terminal.theme import ERROR, WARNING
from surfaces.cli.wizard.components import (
    CUSTOM_MODEL_SENTINEL,
    Choice,
    WizardBack,
    choose,
    choose_model,
    console,
    prompt_value,
    step,
)

if TYPE_CHECKING:
    from surfaces.shared.llm_setup.catalog import ProviderOption


def endpoint_env(provider: ProviderOption) -> dict[str, str]:
    """Return Azure endpoint env vars, using the default API version when unset."""
    return {
        provider.endpoint_env: os.getenv(provider.endpoint_env, "").strip(),
        provider.api_version_env: resolve_azure_openai_api_version(),
    }


def prompt_endpoint_settings(provider: ProviderOption) -> dict[str, str] | None:
    """Collect Azure OpenAI resource URL during onboarding."""
    if not provider.endpoint_env or not provider.api_version_env:
        return {}

    step("Azure endpoint")
    try:
        base_url = prompt_value(
            f"Azure OpenAI resource URL ({provider.endpoint_env})",
            default=os.getenv(provider.endpoint_env, provider.credential_default),
            secret=False,
            back_on_cancel=True,
        )
    except WizardBack:
        return None

    normalized_base = normalize_azure_openai_base_url(base_url)
    if not normalized_base:
        console.print(f"[{ERROR}]Azure OpenAI resource URL is required.[/]")
        return None
    return {
        provider.endpoint_env: normalized_base,
        provider.api_version_env: resolve_azure_openai_api_version(),
    }


def ensure_endpoint_settings(provider: ProviderOption) -> dict[str, str] | None:
    """Return Azure endpoint env vars, prompting when missing."""
    from core.llm.providers.azure_openai import azure_openai_endpoint_configured

    if not is_azure_openai_provider(provider.value):
        return {}
    if azure_openai_endpoint_configured():
        return endpoint_env(provider)
    return prompt_endpoint_settings(provider)


def choose_azure_deployment(
    *,
    default: str | None,
    model_env: str = "AZURE_OPENAI_REASONING_MODEL",
    back_on_cancel: bool = False,
) -> str:
    """Prompt for an Azure OpenAI deployment name from the user's resource."""
    step("Deployment")

    resolved_default = (default or "").strip()
    deployments = discover_azure_openai_deployments_from_env()
    if not deployments:
        console.print(
            f"[{WARNING}]Could not list deployments from your Azure resource. "
            "Enter the deployment name from the Azure portal.[/]"
        )
        return prompt_value(
            f"Azure OpenAI deployment name ({model_env})",
            default=resolved_default,
            allow_empty=False,
            back_on_cancel=back_on_cancel,
        )

    deployment_choices = [
        Choice(value=deployment, label=deployment, hint="deployment") for deployment in deployments
    ]
    extra_choices: list[Choice] = []
    if resolved_default and resolved_default not in deployments:
        extra_choices.append(Choice(value=resolved_default, label=resolved_default, hint="current"))

    custom_choice = Choice(
        value=CUSTOM_MODEL_SENTINEL,
        label="Enter custom deployment name",
        hint="type deployment name from Azure portal",
    )
    choices = deployment_choices + extra_choices + [custom_choice]
    default_value = resolved_default or deployments[0]
    if default_value and not any(choice.value == default_value for choice in choices):
        default_value = deployments[0]

    selection = choose(
        "Choose Azure OpenAI deployment",
        choices,
        default=default_value or None,
        back_on_cancel=back_on_cancel,
    )
    if selection != CUSTOM_MODEL_SENTINEL:
        return selection

    return prompt_value(
        f"Custom Azure OpenAI deployment name ({model_env})",
        default=resolved_default,
        allow_empty=False,
        back_on_cancel=back_on_cancel,
    )


def choose_provider_model(
    provider: ProviderOption,
    *,
    default: str | None,
    prompt_label: str | None = None,
    back_on_cancel: bool = False,
) -> str:
    """Prompt for a model or Azure deployment after provider credentials are set."""
    if is_azure_openai_provider(provider.value):
        return choose_azure_deployment(
            default=default,
            model_env=provider.model_env,
            back_on_cancel=back_on_cancel,
        )
    return choose_model(
        provider,
        default=default,
        prompt_label=prompt_label,
        back_on_cancel=back_on_cancel,
    )
