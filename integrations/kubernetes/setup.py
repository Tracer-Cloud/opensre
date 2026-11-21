"""What Kubernetes needs before it is considered configured.

Kubeconfig can be a file path *or* pasted inline YAML. A picker scopes which
of those two is collected; the either/or rule lives in the Kubernetes client
probe (``is_configured`` / ``missing``), so setup and health checks agree for
any surface that skips the picker. Context, namespace and the optional
namespace allowlist are always asked; leaving the allowlist empty keeps the
agent on the default namespace only.

Inline YAML is mirrored to the keyring (it embeds bearer tokens / client keys);
the file-path field stays in ``.env``.
"""

from __future__ import annotations

from config.constants.kubernetes import (
    KUBECONFIG_CONTENT_ENV,
    KUBECONFIG_CONTEXT_ENV,
    KUBECONFIG_NAMESPACE_ENV,
    KUBECONFIG_NAMESPACES_ENV,
    KUBECONFIG_PATH_ENV,
)
from integrations.kubernetes.verifier import verify_kubernetes
from integrations.setup_flow import IntegrationSetupSpec, SetupField, SetupMode

KUBECONFIG_PATH_FIELD = "kubeconfig_path"
KUBECONFIG_FIELD = "kubeconfig"
CONTEXT_FIELD = "context"
NAMESPACE_FIELD = "namespace"
ALLOWED_NAMESPACES_FIELD = "allowed_namespaces"

KUBERNETES_SETUP = IntegrationSetupSpec(
    service="kubernetes",
    fields=(
        SetupField(
            name=KUBECONFIG_PATH_FIELD,
            label="Kubeconfig file path",
            prompt="Kubeconfig file path (e.g. ~/.kube/config)",
            env_var=KUBECONFIG_PATH_ENV,
            required=False,
        ),
        SetupField(
            name=KUBECONFIG_FIELD,
            label="Inline kubeconfig YAML",
            prompt="Paste raw kubeconfig YAML content",
            env_var=KUBECONFIG_CONTENT_ENV,
            # Keyring: the YAML embeds bearer tokens / client keys / certs.
            # ``is_sensitive_env_key("KUBECONFIG_CONTENT")`` routes it there;
            # the path-only ``KUBECONFIG`` field stays in ``.env``.
            secret=True,
            required=False,
        ),
        SetupField(
            name=CONTEXT_FIELD,
            label="Kubeconfig context",
            prompt=(
                "Kubeconfig context to use (leave empty to use the current-context from the file)"
            ),
            env_var=KUBECONFIG_CONTEXT_ENV,
            required=False,
        ),
        SetupField(
            name=NAMESPACE_FIELD,
            label="Default namespace",
            env_var=KUBECONFIG_NAMESPACE_ENV,
            default="default",
        ),
        SetupField(
            name=ALLOWED_NAMESPACES_FIELD,
            label="Additional allowed namespaces",
            prompt=(
                "Other namespaces the agent may inspect, comma-separated "
                "(leave empty to allow only the default namespace)"
            ),
            env_var=KUBECONFIG_NAMESPACES_ENV,
            required=False,
        ),
    ),
    mode_prompt="Kubeconfig source:",
    modes=(
        SetupMode(
            value="path",
            label="File path on disk",
            fields=(KUBECONFIG_PATH_FIELD,),
        ),
        SetupMode(
            value="inline",
            label="Paste inline YAML",
            fields=(KUBECONFIG_FIELD,),
        ),
    ),
    verify=verify_kubernetes,
)

__all__ = [
    "ALLOWED_NAMESPACES_FIELD",
    "CONTEXT_FIELD",
    "KUBECONFIG_FIELD",
    "KUBECONFIG_PATH_FIELD",
    "KUBERNETES_SETUP",
    "NAMESPACE_FIELD",
]
