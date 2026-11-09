"""Helm CLI client — read-only release inspection for investigations."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from integrations.config_models import HelmIntegrationConfig
from integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_CMD_TIMEOUT = 90.0
_PROBE_LIST_TIMEOUT = 45.0
_PROBE_VERSION_TIMEOUT = 15.0
_DEFAULT_MANIFEST_CHARS = 600_000


def _helm_client_major_version(version_client_output: str) -> int | None:
    """Best-effort major Helm client version from ``helm version`` stdout."""
    text = version_client_output
    if m := re.search(r'SemVer:"v(\d+)', text):
        return int(m.group(1))
    if m := re.search(r'Version:"v(\d+)', text):
        return int(m.group(1))
    return None


def _manifest_char_cap() -> int:
    """Max manifest size; override with HELM_MANIFEST_MAX_CHARS (integer, min 1024)."""
    raw = (os.getenv("HELM_MANIFEST_MAX_CHARS") or "").strip()
    if raw.isdigit():
        return max(1024, int(raw))
    return _DEFAULT_MANIFEST_CHARS


class HelmClient:
    """Runs Helm 3 CLI commands with explicit kubeconfig/context and timeouts.

    Requires Helm 3.x (``helm version`` is checked during :meth:`probe_access`).

    Environment:
        ``HELM_MANIFEST_MAX_CHARS`` — optional integer; minimum 1024; caps ``get_manifest``
        output size (default 600_000). When truncated, the result sets ``truncated=True``.
    """

    def __init__(self, config: HelmIntegrationConfig) -> None:
        self._config = config

    @property
    def is_configured(self) -> bool:
        return self._resolved_helm_path() is not None

    def _resolved_helm_path(self) -> str | None:
        raw = (self._config.helm_path or "helm").strip() or "helm"
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            return str(candidate)
        return shutil.which(raw)

    def _kube_flags(self) -> list[str]:
        flags: list[str] = []
        ctx = self._config.kube_context.strip()
        if ctx:
            flags.extend(["--kube-context", ctx])
        kc = self._config.kubeconfig.strip()
        if kc:
            flags.extend(["--kubeconfig", str(Path(kc).expanduser())])
        return flags

    def _base_cmd(self) -> list[str] | None:
        hp = self._resolved_helm_path()
        if hp is None:
            return None
        return [hp, *self._kube_flags()]

    def _run(self, args: list[str], *, timeout: float) -> tuple[int, str, str]:
        base = self._base_cmd()
        if base is None:
            path_hint = (self._config.helm_path or "helm").strip() or "helm"
            return 127, "", f"helm executable not found ({path_hint!r})"
        cmd = [*base, *args]
        logger.debug("helm subprocess: %s subcommands", len(args))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired:
            return 124, "", "helm command timed out"
        except OSError as exc:
            return 1, "", f"helm subprocess failed: {exc}"
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def probe_access(self) -> ProbeResult:
        if self._resolved_helm_path() is None:
            path = (self._config.helm_path or "helm").strip() or "helm"
            return ProbeResult.missing(
                f"Helm binary not found ({path!r}). Install Helm or set helm_path to a binary."
            )
        code, ver_out, err = self._run(["version"], timeout=_PROBE_VERSION_TIMEOUT)
        if code != 0:
            detail = (err or "unknown error").strip()
            return ProbeResult.failed(f"helm version failed (exit {code}): {detail}")

        combined_ver = f"{ver_out}\n{err or ''}"
        major = _helm_client_major_version(combined_ver)
        if major is not None and major < 3:
            return ProbeResult.failed(
                "Helm 3.x is required for this integration; `helm version` "
                f"reports a Helm {major}.x client. Install Helm 3 or point helm_path at a Helm 3 "
                "binary."
            )

        code, out, err = self._run(
            ["list", "-A", "--max", "1", "-o", "json"],
            timeout=_PROBE_LIST_TIMEOUT,
        )
        if code != 0:
            detail = (err or out or "cluster unreachable or kubeconfig missing").strip()
            return ProbeResult.failed(f"Helm cannot list releases: {detail}")

        stdout = (out or "").strip()
        if not stdout:
            return ProbeResult.failed(
                "Helm list returned empty output; expected a JSON array of releases."
            )
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            snippet = stdout[:200].replace("\n", " ")
            return ProbeResult.failed(
                f"Helm list output is not valid JSON ({exc}; stdout starts with {snippet!r})"
            )
        if not isinstance(parsed, list):
            return ProbeResult.failed(
                "Helm list -o json must return a JSON array of releases, "
                f"not {type(parsed).__name__}."
            )

        return ProbeResult.passed("Helm CLI is available and can reach the Kubernetes cluster.")

    def list_releases(
        self,
        *,
        namespace: str = "",
        all_namespaces: bool = False,
        max_releases: int = 256,
    ) -> dict[str, Any]:
        cap = max(1, min(max_releases, MAX_MESSAGE_SIZE))
        args = ["list", "-o", "json", "--max", str(cap)]
        if all_namespaces:
            args.append("-A")
        elif namespace.strip():
            args.extend(["-n", namespace.strip()])
        else:
            args.append("-A")

        ns_filter = namespace.strip()
        # Match the branches above: `-A` when listing every namespace (explicit flag or none set).
        listed_all_namespaces = bool(all_namespaces) or not bool(ns_filter)

        code, out, err = self._run(args, timeout=_DEFAULT_CMD_TIMEOUT)
        if code != 0:
            return {
                "success": False,
                "error": (err or out).strip(),
                "releases": [],
                "all_namespaces": listed_all_namespaces,
                "namespace": ns_filter,
            }
        try:
            parsed = json.loads(out or "[]")
        except json.JSONDecodeError:
            return {
                "success": False,
                "error": "invalid JSON from helm list",
                "releases": [],
                "all_namespaces": listed_all_namespaces,
                "namespace": ns_filter,
            }
        if not isinstance(parsed, list):
            return {
                "success": False,
                "error": "unexpected helm list shape",
                "releases": [],
                "all_namespaces": listed_all_namespaces,
                "namespace": ns_filter,
            }
        return {
            "success": True,
            "error": "",
            "releases": parsed,
            "all_namespaces": listed_all_namespaces,
            "namespace": ns_filter,
        }

    def release_status(self, release: str, namespace: str) -> dict[str, Any]:
        rel = release.strip()
        ns = namespace.strip() or "default"
        if not rel:
            return {"success": False, "error": "release name is required", "status": {}}
        code, out, err = self._run(
            ["status", rel, "-n", ns, "-o", "json"],
            timeout=_DEFAULT_CMD_TIMEOUT,
        )
        if code != 0:
            return {"success": False, "error": (err or out).strip(), "status": {}}
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            return {"success": False, "error": "invalid JSON from helm status", "status": {}}
        if not isinstance(payload, dict):
            return {"success": False, "error": "unexpected helm status shape", "status": {}}
        return {
            "success": True,
            "error": "",
            "release": rel,
            "namespace": ns,
            "status": payload,
        }

    def release_history(
        self,
        release: str,
        namespace: str,
        *,
        max_revisions: int = 10,
    ) -> dict[str, Any]:
        rel = release.strip()
        ns = namespace.strip() or "default"
        limit = max(1, min(max_revisions, 64))
        if not rel:
            return {"success": False, "error": "release name is required", "history": []}
        code, out, err = self._run(
            ["history", rel, "-n", ns, "-o", "json", "--max", str(limit)],
            timeout=_DEFAULT_CMD_TIMEOUT,
        )
        if code != 0:
            return {"success": False, "error": (err or out).strip(), "history": []}
        try:
            parsed = json.loads(out or "[]")
        except json.JSONDecodeError:
            return {"success": False, "error": "invalid JSON from helm history", "history": []}
        if not isinstance(parsed, list):
            return {"success": False, "error": "unexpected helm history shape", "history": []}
        return {
            "success": True,
            "error": "",
            "release": rel,
            "namespace": ns,
            "history": parsed,
        }

    def get_values(
        self,
        release: str,
        namespace: str,
        *,
        all_values: bool = False,
    ) -> dict[str, Any]:
        rel = release.strip()
        ns = namespace.strip() or "default"
        if not rel:
            return {"success": False, "error": "release name is required", "values": {}}
        args = ["get", "values", rel, "-n", ns, "-o", "json"]
        if all_values:
            args.append("--all")
        code, out, err = self._run(args, timeout=_DEFAULT_CMD_TIMEOUT)
        if code != 0:
            return {"success": False, "error": (err or out).strip(), "values": {}}
        try:
            raw = json.loads(out or "{}")
        except json.JSONDecodeError:
            return {"success": False, "error": "invalid JSON from helm get values", "values": {}}
        # helm get values -o json emits JSON null when the release has no user-supplied values.
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            return {"success": False, "error": "unexpected helm values shape", "values": {}}
        parsed = raw
        return {
            "success": True,
            "error": "",
            "release": rel,
            "namespace": ns,
            "values": parsed,
            "all_values": all_values,
        }

    def get_manifest(self, release: str, namespace: str) -> dict[str, Any]:
        rel = release.strip()
        ns = namespace.strip() or "default"
        if not rel:
            return {"success": False, "error": "release name is required", "manifest": ""}
        code, out, err = self._run(
            ["get", "manifest", rel, "-n", ns],
            timeout=_DEFAULT_CMD_TIMEOUT,
        )
        if code != 0:
            return {
                "success": False,
                "error": (err or out).strip(),
                "manifest": "",
                "truncated": False,
            }
        text = out or ""
        truncated = False
        cap = _manifest_char_cap()
        if len(text) > cap:
            text = text[:cap]
            truncated = True
        return {
            "success": True,
            "error": "",
            "release": rel,
            "namespace": ns,
            "manifest": text,
            "truncated": truncated,
        }
