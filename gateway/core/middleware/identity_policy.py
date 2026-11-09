"""Load and persist a chat transport's inbound identity policy.

Stored on the platform's integration record
(``credentials["identity_policy"]``). Transports pass the platform name;
this module talks only to the integration store and messaging-security
primitives so ``gateway.core`` stays free of transport imports.
"""

from __future__ import annotations

from typing import Any, Protocol

from integrations.messaging_security import MessagingIdentityPolicy
from integrations.store import get_integration, upsert_instance


class InboundPolicyDecision(Protocol):
    """The two fields any transport's inbound decision carries for persistence."""

    @property
    def persist_policy(self) -> bool:
        """Whether this decision changed the policy and asked for a write."""

    @property
    def updated_policy(self) -> MessagingIdentityPolicy | None:
        """The policy to store when ``persist_policy`` is set."""


def load_identity_policy(
    platform: str,
) -> tuple[dict[str, Any] | None, MessagingIdentityPolicy]:
    """Return the platform's stored record and its identity policy.

    A platform with no stored record (or no policy on it) gets a permissive
    default — inbound enabled, empty allowlist — so first-run transports work
    before anyone has configured identities.
    """
    record = get_integration(platform)
    if record is None:
        return None, MessagingIdentityPolicy(inbound_enabled=True)
    credentials = record.get("credentials", {})
    raw_policy = credentials.get("identity_policy")
    if raw_policy and isinstance(raw_policy, dict):
        return record, MessagingIdentityPolicy.model_validate(raw_policy)
    return record, MessagingIdentityPolicy(inbound_enabled=True)


def save_identity_policy(
    platform: str,
    record: dict[str, Any] | None,
    policy: MessagingIdentityPolicy,
) -> None:
    """Store ``policy`` on the platform's record, preserving instance identity."""
    instances = record.get("instances", []) if record else []
    first_instance = instances[0] if instances else {}
    instance_name = (
        first_instance.get("name", "default") if isinstance(first_instance, dict) else "default"
    )
    credentials = dict(record.get("credentials", {})) if record else {}
    credentials["identity_policy"] = policy.model_dump(mode="json")
    upsert_instance(
        platform,
        {
            "name": instance_name,
            "tags": first_instance.get("tags", {}) if isinstance(first_instance, dict) else {},
            "credentials": credentials,
        },
        record_id=record.get("id") if record else None,
    )


def persist_policy_if_needed(platform: str, decision: InboundPolicyDecision) -> None:
    """Write the decision's updated policy, when it carries one."""
    if not decision.persist_policy or decision.updated_policy is None:
        return
    record, _ = load_identity_policy(platform)
    save_identity_policy(platform, record, decision.updated_policy)


__all__ = [
    "InboundPolicyDecision",
    "load_identity_policy",
    "persist_policy_if_needed",
    "save_identity_policy",
]
