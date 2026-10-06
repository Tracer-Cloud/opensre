"""Tool parameters that only configuration may set, never the model.

A tool's arguments merge configured values with model input. Connection
fields, credentials, local transports and TLS switches belong to the
configuration, so these names are hidden from every model-facing schema and
any model-supplied value for them is dropped.
"""

from __future__ import annotations

from typing import Final

#: Credentials, destinations, local transports and TLS switches.
CONFIG_ONLY_TOOL_PARAMS: Final[frozenset[str]] = frozenset(
    {
        # Credentials
        "api_key",
        "api_token",
        "app_key",
        "app_password",
        "bearer_token",
        "coralogix_api_key",
        "github_token",
        "honeycomb_api_key",
        "jenkins_token",
        "jenkins_user",
        "password",
        "sasl_password",
        "sasl_username",
        "sentry_token",
        "token",
        "user",
        "username",
        # Destinations
        "base_url",
        "coralogix_base_url",
        "endpoint",
        "honeycomb_base_url",
        "jenkins_url",
        "sentry_url",
        "site",
        "webhook_url",
        # Local transports
        "helm_path",
        "kubeconfig",
        "sentry_args",
        "sentry_command",
        "sentry_mode",
        # TLS and auth-method switches
        "auth_source",
        "security_protocol",
        "ssl",
        "tls",
        "verify_ssl",
    }
)

#: Per-tool exceptions: the value names a search target, not a connection.
MODEL_SUPPLIED_CONFIG_PARAMS: Final[dict[str, frozenset[str]]] = {
    # CloudTrail filters events by the acting IAM user.
    "lookup_cloudtrail_events": frozenset({"username"}),
    # Only the org slug and issue id are read from the link; the host comes from config.
    "fix_sentry_issue": frozenset({"sentry_url"}),
    "fix_sentry_issue_start": frozenset({"sentry_url"}),
}


def config_only_params(tool_name: str, injected_params: tuple[str, ...] = ()) -> frozenset[str]:
    """Names ``tool_name`` must take from configuration, never from the model."""
    allowed = MODEL_SUPPLIED_CONFIG_PARAMS.get(tool_name, frozenset())
    return (CONFIG_ONLY_TOOL_PARAMS - allowed) | frozenset(injected_params)


__all__ = ["CONFIG_ONLY_TOOL_PARAMS", "MODEL_SUPPLIED_CONFIG_PARAMS", "config_only_params"]
