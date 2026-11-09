"""OpenSearch environment variable names and setup commands."""

from __future__ import annotations

OPENSEARCH_URL_ENV = "OPENSEARCH_URL"
OPENSEARCH_API_KEY_ENV = "OPENSEARCH_API_KEY"
OPENSEARCH_USERNAME_ENV = "OPENSEARCH_USERNAME"
OPENSEARCH_PASSWORD_ENV = "OPENSEARCH_PASSWORD"
#: Shell command that opens the OpenSearch/Elasticsearch setup wizard.
OPENSEARCH_INTEGRATION_SETUP_SLASH = "/integrations setup opensearch"
#: Same wizard from a terminal that is not already inside the interactive shell.
OPENSEARCH_INTEGRATION_SETUP_CLI = "opensre integrations setup opensearch"

__all__ = [
    "OPENSEARCH_API_KEY_ENV",
    "OPENSEARCH_INTEGRATION_SETUP_CLI",
    "OPENSEARCH_INTEGRATION_SETUP_SLASH",
    "OPENSEARCH_PASSWORD_ENV",
    "OPENSEARCH_URL_ENV",
    "OPENSEARCH_USERNAME_ENV",
]
