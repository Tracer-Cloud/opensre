"""Pick the credential for the gateway's credit-metering calls to opensre-webapp.

Two credentials exist: this silo's org-scoped machine token (minted from
``CLERK_MACHINE_SECRET_KEY``) and the shared ``AGENT_USAGE_SECRET``. Webapp
routes differ in which they accept, so this module exposes one accessor per
acceptance rule rather than a single "the token" — a caller that picks the
wrong one gets a 401 that looks like an empty result.

Lives here because ``credits_client`` is the only consumer: the vault reads the
shared secret itself (``integrations.webapp_vault``), and nothing else mints a
machine token.

  - :func:`webapp_bearer_token`  — routes accepting either credential
  - :func:`webapp_machine_token` — routes accepting only the machine token
  - :func:`webapp_shared_secret` — routes accepting only the shared secret

Lives here rather than in ``config/`` because minting reaches
``clerk_tokens``: putting the choice in the constants leaf would make ``config``
depend upward on ``integrations`` and form an import cycle.
"""

from __future__ import annotations

import logging
import os

import gateway.core.billing.clerk_tokens as clerk_tokens
from config.constants.billing import MACHINE_SECRET_ENV, USAGE_SECRET_ENV

logger = logging.getLogger(__name__)


def webapp_shared_secret() -> str:
    """Shared fleet secret, for routes that accept only that credential.

    ``/api/agent/integrations`` compares the bearer against
    ``AGENT_USAGE_SECRET`` alone, so a machine token there is a 401. Returns ""
    when unset. Callers decide whether that means an intentionally disabled
    self-hosted feature or a hosted configuration error.
    """
    return (os.getenv(USAGE_SECRET_ENV) or "").strip()


def webapp_machine_token() -> str:
    """Org-scoped machine token, for routes that accept only that credential.

    Returns "" when the machine secret is unset or the token cannot be
    obtained. Callers must not substitute the shared secret: routes requiring
    this credential reject it, and the 401 is indistinguishable from an empty
    result. Never raises; hosted callers must treat an empty result as an
    unavailable authentication path.
    """
    if not os.getenv(MACHINE_SECRET_ENV):
        return ""
    try:
        # Module attribute (not a from-import) so tests can patch the mint.
        return clerk_tokens.webapp_access_token()
    except Exception as exc:  # noqa: BLE001 - classify mint failure at the caller
        logger.warning("[webapp-auth] machine token mint raised (%s)", type(exc).__name__)
        return ""


def webapp_bearer_token() -> str:
    """Credential for routes that accept either credential.

    Prefers the org-scoped machine token and falls back to the shared secret.
    Returns "" when neither is available. A hosted credit caller must fail
    closed rather than interpret that as authorization to run unmetered work.
    """
    return webapp_machine_token() or webapp_shared_secret()
