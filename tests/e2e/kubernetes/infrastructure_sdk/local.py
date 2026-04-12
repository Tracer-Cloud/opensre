"""Kind cluster and kubectl helpers for local Kubernetes testing."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request


def _run(
    cmd: list[str], *, check: bool = True, capture: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def check_prerequisites() -> list[str]:
    """Return list of missing prerequisites (empty = all good)."""
    missing = []
    for tool in ("kind", "kubectl", "docker", "helm"):
        if shutil.which(tool) is None:
            missing.append(tool)
    return missing


def check_prerequisites_basic() -> list[str]:
    """Return list of missing prerequisites for basic (non-Helm) tests."""
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
                "kubectl",
                "get",
                "job",
                job_name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.status.conditions[*].type}",
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
# Datadog Helm chart helpers
# ---------------------------------------------------------------------------

DATADOG_HELM_REPO = "https://helm.datadoghq.com"
DATADOG_HELM_RELEASE = "datadog"
DATADOG_HELM_CHART = "datadog/datadog"


def add_datadog_helm_repo() -> None:
    _run(["helm", "repo", "add", "datadog", DATADOG_HELM_REPO], check=False)
    _run(["helm", "repo", "update", "datadog"], capture=False)


def deploy_datadog_helm(values_file: str, namespace: str) -> None:
    """Install Datadog via official Helm chart."""
    api_key = os.environ.get("DD_API_KEY", "")
    if not api_key:
        raise OSError("DD_API_KEY environment variable is required")

    add_datadog_helm_repo()

    _run(["kubectl", "create", "namespace", namespace], check=False)

    cmd = [
        "helm",
        "upgrade",
        "--install",
        DATADOG_HELM_RELEASE,
        DATADOG_HELM_CHART,
        "-n",
        namespace,
        "-f",
        values_file,
        "--set",
        f"datadog.apiKey={api_key}",
        "--wait",
        "--timeout",
        "3m",
    ]

    site = os.environ.get("DD_SITE", "")
    if site:
        cmd.extend(["--set", f"datadog.site={site}"])

    print("Installing Datadog Helm chart...")
    try:
        _run(cmd, capture=False)
    except subprocess.CalledProcessError:
        # Datadog operator/cluster-agent can exceed helm --wait timeout on fresh installs.
        # Retry without blocking wait and let wait_for_datadog_agent handle readiness.
        cmd_no_wait = [arg for arg in cmd if arg not in {"--wait", "--timeout", "3m"}]
        print("Helm wait timed out, retrying install without --wait...")
        _run(cmd_no_wait, capture=False)
    print("Datadog Helm chart installed")


def wait_for_datadog_agent(namespace: str, timeout: int = 180) -> bool:
    """Wait for Datadog Agent DaemonSet to have at least one ready pod."""
    print("Waiting for Datadog Agent to be ready...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            [
                "kubectl",
                "get",
                "daemonset",
                "-n",
                namespace,
                "-l",
                "app.kubernetes.io/component=agent",
                "-o",
                "jsonpath={.items[0].status.numberReady}",
            ],
            check=False,
        )
        ready = result.stdout.strip()
        if ready and ready.isdigit() and int(ready) > 0:
            print(f"Datadog Agent ready ({ready} pod(s))")
            return True
        time.sleep(5)

    print(f"Datadog Agent not ready after {timeout}s")
    return False


# ---------------------------------------------------------------------------
# Datadog Monitor API helpers
# ---------------------------------------------------------------------------


def _dd_api_headers() -> dict[str, str]:
    api_key = os.environ.get("DD_API_KEY", "")
    app_key = os.environ.get("DD_APP_KEY", "")
    if not api_key or not app_key:
        raise OSError("DD_API_KEY and DD_APP_KEY are required for monitor management")
    return {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
        "Content-Type": "application/json",
    }


def _dd_api_request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
) -> dict:
    """Make a request to the Datadog API. Returns parsed JSON response."""
    site = os.environ.get("DD_SITE", "datadoghq.com")
    url = f"https://api.{site}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=_dd_api_headers(), method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def load_monitor_definitions(yaml_path: str) -> list[dict]:
    """Load monitor definitions from a YAML file."""
    import yaml

    with open(yaml_path) as f:
        doc = yaml.safe_load(f)
    return doc.get("monitors", [])


def _find_monitor_by_name(name: str) -> dict | None:
    """Find an existing monitor by exact name."""
    encoded = urllib.parse.quote(name)
    monitors = _dd_api_request("GET", f"/api/v1/monitor?name={encoded}")
    for m in monitors:
        if m.get("name") == name:
            return m
    return None


def create_or_update_monitor(monitor_def: dict) -> dict:
    """Create or update a Datadog monitor. Idempotent by name."""
    name = monitor_def["name"]
    existing = _find_monitor_by_name(name)

    payload = {
        "name": name,
        "type": monitor_def["type"],
        "query": monitor_def["query"],
        "message": monitor_def.get("message", ""),
        "tags": monitor_def.get("tags", []),
        "priority": monitor_def.get("priority"),
        "options": monitor_def.get("options", {}),
    }

    if existing:
        monitor_id = existing["id"]
        print(f"Updating monitor '{name}' (id={monitor_id})")
        return _dd_api_request("PUT", f"/api/v1/monitor/{monitor_id}", body=payload)

    print(f"Creating monitor '{name}'")
    return _dd_api_request("POST", "/api/v1/monitor", body=payload)


def delete_monitor_by_name(name: str) -> bool:
    """Delete a Datadog monitor by name. Returns True if deleted."""
    existing = _find_monitor_by_name(name)
    if not existing:
        return False
    monitor_id = existing["id"]
    print(f"Deleting monitor '{name}' (id={monitor_id})")
    _dd_api_request("DELETE", f"/api/v1/monitor/{monitor_id}")
    return True
