# ======== from tools/helm_tools/ ========

"""Helm CLI investigation tools (Helm 3, read-only)."""

from __future__ import annotations

from typing import Any

from core.tool import BaseTool, SideEffectLevel
from integrations.helm.client_factory import helm_client_for_run
from integrations.helm.unavailable import helm_base_unavailable


class HelmListReleasesTool(BaseTool):
    """List Helm releases in the configured Kubernetes cluster."""

    name = "helm_list_releases"
    source = "helm"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "List Helm releases (JSON metadata) using the local Helm CLI against the "
        "configured kubeconfig/context."
    )
    use_cases = [
        "Finding which Helm release name/namespace to investigate for a failing workload",
        "Correlating incident time with chart revisions across namespaces",
    ]
    requires = ["helm_path"]
    input_schema = {
        "type": "object",
        "properties": {
            "helm_path": {
                "type": "string",
                "default": "helm",
                "description": "Helm binary or path",
            },
            "kube_context": {
                "type": "string",
                "default": "",
                "description": "Optional kubectl context (--kube-context)",
            },
            "kubeconfig": {
                "type": "string",
                "default": "",
                "description": "Optional kubeconfig file path (--kubeconfig)",
            },
            "all_namespaces": {
                "type": "boolean",
                "default": True,
                "description": "When true, list releases in all namespaces (-A)",
            },
            "namespace": {
                "type": "string",
                "default": "",
                "description": "When set (and all_namespaces is false), scope with -n",
            },
            "default_namespace": {
                "type": "string",
                "default": "",
                "description": "Fallback namespace when all_namespaces is false and namespace empty",
            },
            "max_releases": {
                "type": "integer",
                "default": 256,
                "description": "Cap for helm list --max (bounded by the client)",
            },
            "integration_id": {"type": "string", "default": "", "description": "Integration id"},
        },
    }
    outputs = {"releases": "Helm list releases payload (parsed JSON objects)"}

    def is_available(self, sources: dict) -> bool:
        h = sources.get("helm", {})
        return bool(h.get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        h = sources["helm"]
        return {
            "helm_path": str(h.get("helm_path", "helm") or "helm").strip() or "helm",
            "kube_context": str(h.get("kube_context", "")).strip(),
            "kubeconfig": str(h.get("kubeconfig", "")).strip(),
            "default_namespace": str(h.get("default_namespace", "")).strip(),
            "namespace": "",
            "all_namespaces": True,
            "max_releases": 256,
            "integration_id": str(h.get("integration_id", "")).strip(),
        }

    def run(
        self,
        helm_path: str = "helm",
        kube_context: str = "",
        kubeconfig: str = "",
        default_namespace: str = "",
        namespace: str = "",
        all_namespaces: bool = True,
        max_releases: int = 256,
        integration_id: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        del _kwargs
        client = helm_client_for_run(
            helm_path,
            kube_context,
            kubeconfig,
            default_namespace,
            integration_id,
        )
        if client is None:
            merged = helm_base_unavailable("Helm integration parameters failed validation.")
            merged["releases"] = []
            return merged
        if not client.is_configured:
            merged = helm_base_unavailable("Helm binary not found on PATH.")
            merged["releases"] = []
            return merged

        effective_ns = namespace.strip() or default_namespace.strip()
        use_all = all_namespaces or not effective_ns
        result = client.list_releases(
            namespace=effective_ns if not use_all else "",
            all_namespaces=use_all,
            max_releases=max_releases,
        )
        ok = bool(result.get("success"))
        return {
            "source": "helm",
            "available": ok,
            "error": result.get("error", "") if not ok else "",
            **result,
        }


class HelmReleaseStatusTool(BaseTool):
    """Fetch `helm status` for one release (JSON)."""

    name = "helm_release_status"
    source = "helm"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = "Fetch Helm release status (resources, hooks metadata, notes) as structured JSON."
    use_cases = [
        "Checking whether a Helm release is in failed/pending state",
        "Reading chart/app version and last deployment metadata for a release",
    ]
    requires = ["release_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "helm_path": {"type": "string", "default": "helm"},
            "kube_context": {"type": "string", "default": ""},
            "kubeconfig": {"type": "string", "default": ""},
            "release_name": {"type": "string", "description": "Helm release name"},
            "namespace": {
                "type": "string",
                "default": "",
                "description": "Kubernetes namespace for the release (default if empty)",
            },
            "default_namespace": {"type": "string", "default": "", "description": "Fallback ns"},
            "integration_id": {"type": "string", "default": ""},
        },
        "required": ["release_name"],
    }
    outputs = {"status": "helm status -o json payload"}

    def is_available(self, sources: dict) -> bool:
        h = sources.get("helm", {})
        return bool(h.get("connection_verified") and str(h.get("release_name", "")).strip())

    def extract_params(self, sources: dict) -> dict[str, Any]:
        h = sources["helm"]
        ns = str(h.get("namespace", "") or h.get("default_namespace", "")).strip()
        return {
            "helm_path": str(h.get("helm_path", "helm") or "helm").strip() or "helm",
            "kube_context": str(h.get("kube_context", "")).strip(),
            "kubeconfig": str(h.get("kubeconfig", "")).strip(),
            "default_namespace": str(h.get("default_namespace", "")).strip(),
            "release_name": str(h.get("release_name", "")).strip(),
            "namespace": ns,
            "integration_id": str(h.get("integration_id", "")).strip(),
        }

    def run(
        self,
        release_name: str,
        helm_path: str = "helm",
        kube_context: str = "",
        kubeconfig: str = "",
        namespace: str = "",
        default_namespace: str = "",
        integration_id: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        del _kwargs
        client = helm_client_for_run(
            helm_path,
            kube_context,
            kubeconfig,
            default_namespace,
            integration_id,
        )
        if client is None:
            return {
                **helm_base_unavailable("Helm integration parameters failed validation."),
                "status": {},
            }
        if not client.is_configured:
            return {**helm_base_unavailable("Helm binary not found on PATH."), "status": {}}

        rel = release_name.strip()
        ns = namespace.strip() or default_namespace.strip() or "default"
        result = client.release_status(rel, ns)
        ok = bool(result.get("success"))
        payload = {
            "source": "helm",
            "available": ok,
            "error": result.get("error", "") if not ok else "",
            "release": rel,
            "namespace": ns,
            "status": result.get("status") or {},
        }
        return payload


class HelmReleaseHistoryTool(BaseTool):
    """Fetch `helm history` for a release (JSON array)."""

    name = "helm_release_history"
    source = "helm"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = "Fetch Helm revision history (status, chart version, description per revision)."
    use_cases = [
        "Seeing recent failed rollouts or rollbacks for a Helm release",
        "Comparing chart versions between revisions during an incident window",
    ]
    requires = ["release_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "helm_path": {"type": "string", "default": "helm"},
            "kube_context": {"type": "string", "default": ""},
            "kubeconfig": {"type": "string", "default": ""},
            "release_name": {"type": "string"},
            "namespace": {"type": "string", "default": ""},
            "default_namespace": {"type": "string", "default": ""},
            "max_revisions": {"type": "integer", "default": 10},
            "integration_id": {"type": "string", "default": ""},
        },
        "required": ["release_name"],
    }
    outputs = {"history": "helm history revisions"}

    def is_available(self, sources: dict) -> bool:
        h = sources.get("helm", {})
        return bool(h.get("connection_verified") and str(h.get("release_name", "")).strip())

    def extract_params(self, sources: dict) -> dict[str, Any]:
        h = sources["helm"]
        ns = str(h.get("namespace", "") or h.get("default_namespace", "")).strip()
        return {
            "helm_path": str(h.get("helm_path", "helm") or "helm").strip() or "helm",
            "kube_context": str(h.get("kube_context", "")).strip(),
            "kubeconfig": str(h.get("kubeconfig", "")).strip(),
            "default_namespace": str(h.get("default_namespace", "")).strip(),
            "release_name": str(h.get("release_name", "")).strip(),
            "namespace": ns,
            "max_revisions": 10,
            "integration_id": str(h.get("integration_id", "")).strip(),
        }

    def run(
        self,
        release_name: str,
        helm_path: str = "helm",
        kube_context: str = "",
        kubeconfig: str = "",
        namespace: str = "",
        default_namespace: str = "",
        max_revisions: int = 10,
        integration_id: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        del _kwargs
        client = helm_client_for_run(
            helm_path,
            kube_context,
            kubeconfig,
            default_namespace,
            integration_id,
        )
        if client is None:
            return {
                **helm_base_unavailable("Helm integration parameters failed validation."),
                "history": [],
            }
        if not client.is_configured:
            return {**helm_base_unavailable("Helm binary not found on PATH."), "history": []}

        rel = release_name.strip()
        ns = namespace.strip() or default_namespace.strip() or "default"
        result = client.release_history(rel, ns, max_revisions=max_revisions)
        ok = bool(result.get("success"))
        return {
            "source": "helm",
            "available": ok,
            "error": result.get("error", "") if not ok else "",
            "release": rel,
            "namespace": ns,
            "history": result.get("history") or [],
        }


class HelmGetReleaseValuesTool(BaseTool):
    """Fetch merged user-supplied values (`helm get values`)."""

    name = "helm_get_release_values"
    source = "helm"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = "Fetch Helm values for a release as JSON. May include secrets — handle carefully."
    use_cases = [
        "Confirming image tags, replica counts, or feature flags shipped with a chart revision",
        "Comparing effective values against manifest during a misconfiguration investigation",
    ]
    requires = ["release_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "helm_path": {"type": "string", "default": "helm"},
            "kube_context": {"type": "string", "default": ""},
            "kubeconfig": {"type": "string", "default": ""},
            "release_name": {"type": "string"},
            "namespace": {"type": "string", "default": ""},
            "default_namespace": {"type": "string", "default": ""},
            "all_values": {
                "type": "boolean",
                "default": False,
                "description": "When true, pass --all to include computed defaults",
            },
            "integration_id": {"type": "string", "default": ""},
        },
        "required": ["release_name"],
    }
    outputs = {"values": "helm get values -o json object"}

    def is_available(self, sources: dict) -> bool:
        h = sources.get("helm", {})
        return bool(h.get("connection_verified") and str(h.get("release_name", "")).strip())

    def extract_params(self, sources: dict) -> dict[str, Any]:
        h = sources["helm"]
        ns = str(h.get("namespace", "") or h.get("default_namespace", "")).strip()
        return {
            "helm_path": str(h.get("helm_path", "helm") or "helm").strip() or "helm",
            "kube_context": str(h.get("kube_context", "")).strip(),
            "kubeconfig": str(h.get("kubeconfig", "")).strip(),
            "default_namespace": str(h.get("default_namespace", "")).strip(),
            "release_name": str(h.get("release_name", "")).strip(),
            "namespace": ns,
            "all_values": False,
            "integration_id": str(h.get("integration_id", "")).strip(),
        }

    def run(
        self,
        release_name: str,
        helm_path: str = "helm",
        kube_context: str = "",
        kubeconfig: str = "",
        namespace: str = "",
        default_namespace: str = "",
        all_values: bool = False,
        integration_id: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        del _kwargs
        client = helm_client_for_run(
            helm_path,
            kube_context,
            kubeconfig,
            default_namespace,
            integration_id,
        )
        if client is None:
            return {
                **helm_base_unavailable("Helm integration parameters failed validation."),
                "values": {},
            }
        if not client.is_configured:
            return {**helm_base_unavailable("Helm binary not found on PATH."), "values": {}}

        rel = release_name.strip()
        ns = namespace.strip() or default_namespace.strip() or "default"
        result = client.get_values(rel, ns, all_values=all_values)
        ok = bool(result.get("success"))
        return {
            "source": "helm",
            "available": ok,
            "error": result.get("error", "") if not ok else "",
            "release": rel,
            "namespace": ns,
            "values": result.get("values") or {},
            "all_values": all_values,
        }


class HelmGetReleaseManifestTool(BaseTool):
    """Fetch rendered manifest text (`helm get manifest`), size-capped."""

    name = "helm_get_release_manifest"
    source = "helm"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "Fetch the rendered Kubernetes manifest YAML for a Helm release (truncated if huge)."
    )
    use_cases = [
        "Inspecting live rendered resources for a chart after an upgrade incident",
        "Finding unexpected resources created by a Helm release",
    ]
    requires = ["release_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "helm_path": {"type": "string", "default": "helm"},
            "kube_context": {"type": "string", "default": ""},
            "kubeconfig": {"type": "string", "default": ""},
            "release_name": {"type": "string"},
            "namespace": {"type": "string", "default": ""},
            "default_namespace": {"type": "string", "default": ""},
            "integration_id": {"type": "string", "default": ""},
        },
        "required": ["release_name"],
    }
    outputs = {"manifest": "rendered YAML (possibly truncated)"}

    def is_available(self, sources: dict) -> bool:
        h = sources.get("helm", {})
        return bool(h.get("connection_verified") and str(h.get("release_name", "")).strip())

    def extract_params(self, sources: dict) -> dict[str, Any]:
        h = sources["helm"]
        ns = str(h.get("namespace", "") or h.get("default_namespace", "")).strip()
        return {
            "helm_path": str(h.get("helm_path", "helm") or "helm").strip() or "helm",
            "kube_context": str(h.get("kube_context", "")).strip(),
            "kubeconfig": str(h.get("kubeconfig", "")).strip(),
            "default_namespace": str(h.get("default_namespace", "")).strip(),
            "release_name": str(h.get("release_name", "")).strip(),
            "namespace": ns,
            "integration_id": str(h.get("integration_id", "")).strip(),
        }

    def run(
        self,
        release_name: str,
        helm_path: str = "helm",
        kube_context: str = "",
        kubeconfig: str = "",
        namespace: str = "",
        default_namespace: str = "",
        integration_id: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        del _kwargs
        client = helm_client_for_run(
            helm_path,
            kube_context,
            kubeconfig,
            default_namespace,
            integration_id,
        )
        if client is None:
            return {
                **helm_base_unavailable("Helm integration parameters failed validation."),
                "manifest": "",
                "truncated": False,
            }
        if not client.is_configured:
            return {
                **helm_base_unavailable("Helm binary not found on PATH."),
                "manifest": "",
                "truncated": False,
            }

        rel = release_name.strip()
        ns = namespace.strip() or default_namespace.strip() or "default"
        result = client.get_manifest(rel, ns)
        ok = bool(result.get("success"))
        return {
            "source": "helm",
            "available": ok,
            "error": result.get("error", "") if not ok else "",
            "release": rel,
            "namespace": ns,
            "manifest": result.get("manifest") or "",
            "truncated": bool(result.get("truncated", False)),
        }


helm_list_releases = HelmListReleasesTool()
helm_release_status = HelmReleaseStatusTool()
helm_release_history = HelmReleaseHistoryTool()
helm_get_release_values = HelmGetReleaseValuesTool()
helm_get_release_manifest = HelmGetReleaseManifestTool()
