"""Subscription metadata, independent of API-key credentials."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SubscriptionProvider:
    """A subscription connection with an implemented public login flow."""

    value: str
    label: str
    model_env: str
    default_model: str


SUBSCRIPTION_PROVIDERS = (
    SubscriptionProvider("chatgpt", "ChatGPT subscription", "CHATGPT_MODEL", "gpt-6.1-sol"),
)
SUBSCRIPTION_BY_VALUE = {provider.value: provider for provider in SUBSCRIPTION_PROVIDERS}
