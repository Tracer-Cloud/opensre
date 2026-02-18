"""Kind cluster and kubectl helpers for local Kubernetes testing."""

from __future__ import annotations

import os
import shutil
import subprocess
import time


def _run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def check_prerequisites() -> list[str]:
    """Return list of missing prerequisites (empty = all good)."""
    missing = []
    for tool in ("kind", "kubectl", "docker"):
        if shutil.which(tool) is None:
            missing.append(tool)
    return missing


def cluster_exists(name: str) -> bool:
    result = _run(["kind", "get", "clusters"], check=False)
    return name in result.stdout.splitlines()


def create_kind_cluster(name: str) -> None:
    if cluster_exists(name):
        print(f"kind cluster '{name}' already exists, reusing")
        return
    print(f"Creating kind cluster '{name}'...")
    _run(["kind", "create", "cluster", "--name", name, "--wait", "60s"], capture=False)
    print(f"kind cluster '{name}' ready")


def delete_kind_cluster(name: str) -> None:
    if not cluster_exists(name):
        return
    print(f"Deleting kind cluster '{name}'...")
    _run(["kind", "delete", "cluster", "--name", name], capture=False)


def build_image(context_dir: str, tag: str) -> None:
    print(f"Building Docker image '{tag}'...")
    _run(["docker", "build", "-t", tag, context_dir], capture=False)


def load_image(cluster_name: str, tag: str) -> None:
    print(f"Loading image '{tag}' into kind cluster '{cluster_name}'...")
    _run(["kind", "load", "docker-image", tag, "--name", cluster_name], capture=False)


def apply_manifest(path: str) -> None:
    _run(["kubectl", "apply", "-f", path], capture=False)


def delete_manifest(path: str) -> None:
    _run(["kubectl", "delete", "-f", path, "--ignore-not-found"], capture=False)


def wait_for_job(namespace: str, job_name: str, timeout: int = 120) -> str:
    """Wait for a K8s Job to finish. Returns 'complete' or 'failed'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            [
                "kubectl", "get", "job", job_name,
                "-n", namespace,
                "-o", "jsonpath={.status.conditions[*].type}",
            ],
            check=False,
        )
        conditions = result.stdout.strip()
        if "Complete" in conditions:
            return "complete"
        if "Failed" in conditions:
            return "failed"
        time.sleep(2)

    raise TimeoutError(f"Job '{job_name}' did not finish within {timeout}s")


def get_pod_logs(namespace: str, label_selector: str) -> str:
    """Get combined stdout+stderr logs from pods matching the label selector."""
    result = _run(
        ["kubectl", "logs", "-l", label_selector, "-n", namespace, "--all-containers=true"],
        check=False,
    )
    return (result.stdout + result.stderr).strip()


# ---------------------------------------------------------------------------
# Datadog Agent helpers
# ---------------------------------------------------------------------------


def create_datadog_secret(namespace: str) -> None:
    """Create K8s secret for Datadog API key from DD_API_KEY env var."""
    api_key = os.environ.get("DD_API_KEY", "")
    if not api_key:
        raise OSError("DD_API_KEY environment variable is required")

    site = os.environ.get("DD_SITE", "datadoghq.com")

    _run(
        [
            "kubectl", "create", "secret", "generic", "datadog-api-key",
            "-n", namespace,
            f"--from-literal=api-key={api_key}",
            f"--from-literal=site={site}",
        ],
        check=False,
    )
    print(f"Datadog secret created in namespace '{namespace}' (site={site})")


def deploy_datadog_agent(manifests_dir: str, namespace: str) -> None:
    """Deploy Datadog Agent RBAC + DaemonSet."""
    create_datadog_secret(namespace)
    apply_manifest(os.path.join(manifests_dir, "datadog-rbac.yaml"))
    apply_manifest(os.path.join(manifests_dir, "datadog-agent.yaml"))
    print("Datadog Agent manifests applied")


def wait_for_datadog_agent(namespace: str, timeout: int = 120) -> bool:
    """Wait for Datadog Agent DaemonSet to have at least one ready pod."""
    print("Waiting for Datadog Agent to be ready...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            [
                "kubectl", "get", "daemonset", "datadog-agent",
                "-n", namespace,
                "-o", "jsonpath={.status.numberReady}",
            ],
            check=False,
        )
        ready = result.stdout.strip()
        if ready and int(ready) > 0:
            print(f"Datadog Agent ready ({ready} pod(s))")
            return True
        time.sleep(5)

    print(f"Datadog Agent not ready after {timeout}s")
    return False


def delete_datadog_agent(manifests_dir: str) -> None:
    """Remove Datadog Agent resources."""
    delete_manifest(os.path.join(manifests_dir, "datadog-agent.yaml"))
    delete_manifest(os.path.join(manifests_dir, "datadog-rbac.yaml"))
    _run(
        ["kubectl", "delete", "secret", "datadog-api-key", "-n", "tracer-test", "--ignore-not-found"],
        check=False,
    )
