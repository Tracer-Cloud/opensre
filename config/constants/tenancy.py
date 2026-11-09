"""Env names the multi-tenant control plane injects into a gateway silo.

A silo is one gateway task serving one tenant. The control plane's ECS task
definition supplies these so the gateway can say who it is and fetch that
tenant's credentials at startup.

Credentials reach the silo by exactly one of two routes: this tenant's
IntegrationStore v2 secret (:data:`INTEGRATIONS_SECRET_ARN_ENV`), which the
webapp maintains through the control plane, or the webapp's credentials API
(:data:`CREDENTIALS_API_URL_ENV`). The secret is the deployed route and wins
when both are configured; the API is the staged fallback.

Separate from :data:`config.constants.billing.ORGANIZATION_ID_ENV`
(``ORGANIZATION_ID``), which every deployment sets to name the organization
it serves. Two questions, two answers:

* **Provisioning** ("did the control plane provision this silo?") is answered
  by :data:`CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV` — only the control plane
  creates that secret. The organization name cannot answer it, because every
  deployment serves an organization.
* **Serving** ("which organization does this process serve?" — usage
  attribution, credits, Slack principal, webapp vault, mount ownership, and
  credential hydration once the secret says this is a silo) goes through
  :func:`config.constants.organization.organization_id`, which reads
  ``ORGANIZATION_ID``.

The gateway sets the ``OPENSRE_TURN_*`` names itself, on the CLI child process a
turn's slash command starts, to say which member of that organization acted
(:mod:`config.scope_handoff`).
"""

from __future__ import annotations

from typing import Final

#: Where the gateway exchanges its identity for the tenant's credentials.
CREDENTIALS_API_URL_ENV: Final[str] = "OPENSRE_CREDENTIALS_API_URL"

#: Secrets Manager ARN holding this tenant's bootstrap bundle.
CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV: Final[str] = "OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN"

#: Secrets Manager ARN holding this tenant's IntegrationStore v2 blob.
INTEGRATIONS_SECRET_ARN_ENV: Final[str] = "OPENSRE_INTEGRATIONS_SECRET_ARN"

#: Where the hydrated integration store is written inside the silo. The control
#: plane points this at ephemeral container storage so vault credentials never
#: land on the tenant's persistent workspace mount.
INTEGRATIONS_STORE_PATH_ENV: Final[str] = "OPENSRE_INTEGRATIONS_STORE_PATH"

#: The organization and member whose turn started this CLI child process.
TURN_ORGANIZATION_ID_ENV: Final[str] = "OPENSRE_TURN_ORGANIZATION_ID"
TURN_ACTOR_ID_ENV: Final[str] = "OPENSRE_TURN_ACTOR_ID"

__all__ = [
    "CREDENTIALS_API_URL_ENV",
    "CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV",
    "INTEGRATIONS_SECRET_ARN_ENV",
    "INTEGRATIONS_STORE_PATH_ENV",
    "TURN_ACTOR_ID_ENV",
    "TURN_ORGANIZATION_ID_ENV",
]
