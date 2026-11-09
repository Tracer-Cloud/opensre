"""What ServiceNow needs before it is considered configured.

All three credentials are required. The registered verifier refuses plaintext
HTTP to non-loopback hosts and probes ``sys_user`` — the same checks the CLI
and wizard used to reimplement separately.

The resolve step stores the URL the verifier already accepted (trailing slash
stripped, scheme validated) so classify and setup agree on the canonical form.
"""

from __future__ import annotations

from config.constants.servicenow import (
    SERVICENOW_INSTANCE_URL_ENV,
    SERVICENOW_PASSWORD_ENV,
    SERVICENOW_USERNAME_ENV,
)
from infrastructure.text.url_validation import validate_https_or_loopback_http_url
from integrations.servicenow.verifier import verify_servicenow
from integrations.setup_flow import IntegrationSetupSpec, ResolvedCredentials, SetupField

INSTANCE_URL_FIELD = "instance_url"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"


def _resolve_instance_url(credentials: dict[str, str | None]) -> ResolvedCredentials:
    """Persist the normalized URL the verifier already accepted."""
    try:
        cleaned = validate_https_or_loopback_http_url(
            str(credentials.get(INSTANCE_URL_FIELD) or "").strip().rstrip("/"),
            service_name="ServiceNow",
            field_name="instance URL",
        )
    except ValueError as err:
        return ResolvedCredentials(credentials={}, error=str(err))
    return ResolvedCredentials(
        credentials={**credentials, INSTANCE_URL_FIELD: cleaned},
    )


SERVICENOW_SETUP = IntegrationSetupSpec(
    service="servicenow",
    fields=(
        SetupField(
            name=INSTANCE_URL_FIELD,
            label="Instance URL",
            prompt="Instance URL (e.g. https://dev12345.service-now.com)",
            env_var=SERVICENOW_INSTANCE_URL_ENV,
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            env_var=SERVICENOW_USERNAME_ENV,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            env_var=SERVICENOW_PASSWORD_ENV,
            secret=True,
        ),
    ),
    verify=verify_servicenow,
    resolve=_resolve_instance_url,
)

__all__ = [
    "INSTANCE_URL_FIELD",
    "PASSWORD_FIELD",
    "SERVICENOW_SETUP",
    "USERNAME_FIELD",
]
