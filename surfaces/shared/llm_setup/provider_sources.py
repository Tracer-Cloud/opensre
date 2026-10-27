"""Source labels derived from structured provider authentication metadata."""

from surfaces.shared.llm_setup.catalog import ProviderOption, WizardCredentialKind


def source_label(provider: ProviderOption) -> str:
    kinds = {
        WizardCredentialKind.API_KEY: "API billing",
        WizardCredentialKind.SUBSCRIPTION: "Subscription",
        WizardCredentialKind.CLI: "CLI-managed",
        WizardCredentialKind.NONE: "Ambient credentials",
        WizardCredentialKind.HOST: "Local",
    }
    return f"{provider.label.removesuffix(' API key')} · {kinds[provider.credential_kind]}"
