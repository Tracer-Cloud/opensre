"""Env-var names and tunables for per-org credit metering."""

from __future__ import annotations

from typing import Final

# Injected by the org-silo infra (ECS task definition). No URL explicitly means
# self-hosted metering is disabled; a URL without auth/org is a hosted error.
WEBAPP_URL_ENV: Final[str] = "OPENSRE_WEBAPP_URL"

#: The organization this deployment serves — usage attribution, credits
#: metering, Slack principal resolution, and the fail-closed mount-ownership
#: check in ``config.constants.paths``. The control plane injects it into every
#: silo. Read it through ``config.constants.organization.organization_id``
#: rather than the variable, so there is a single answer to the question.
ORGANIZATION_ID_ENV: Final[str] = "ORGANIZATION_ID"

# Silo → webapp auth. The silo mints a short-lived M2M token (`mt_…`) from its
# Clerk machine secret key; the webapp verifies that token against the org's
# bound machine, so it is org-scoped and cannot act as another tenant. The
# shared secret is the fallback credential, accepted by the webapp only while
# its own env var is set.
MACHINE_SECRET_ENV: Final[str] = "CLERK_MACHINE_SECRET_KEY"
USAGE_SECRET_ENV: Final[str] = "AGENT_USAGE_SECRET"

# Clerk Backend API base for minting M2M tokens; override for testing/self-host.
CLERK_API_BASE_URL_ENV: Final[str] = "CLERK_API_BASE_URL"
CLERK_API_BASE_URL_DEFAULT: Final[str] = "https://api.clerk.com"

CREDITS_HTTP_TIMEOUT_SECONDS: Final[float] = 5.0

# Idempotency header for the consume ledger. The polling transports replay a
# delivery by design after a restart, so the same delivery must carry the same
# key and debit at most once. The gateway derives the key from the transport's
# stable per-delivery id; the webapp is what makes the repeat safe.
CREDITS_IDEMPOTENCY_HEADER: Final[str] = "Idempotency-Key"

# Minted M2M tokens are short-lived and cached in-process; refresh slightly
# before expiry so a token can't lapse mid-request.
MACHINE_TOKEN_TTL_SECONDS: Final[int] = 3600
MACHINE_TOKEN_REFRESH_MARGIN_SECONDS: Final[int] = 60
