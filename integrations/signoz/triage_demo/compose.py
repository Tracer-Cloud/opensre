"""Generate namespace-owned Compose files, publish loopback ports, freeze images."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from config.constants.triage_demo import (
    DEMO_COLLECTOR_VERSION,
    DEMO_LABEL,
    DEMO_OTEL_VERSION,
    DEMO_PREFIX,
    DEMO_SIGNOZ_VERSION,
)
from integrations.signoz.triage_demo.artifacts import run


def owned(spec: dict[str, Any], namespace: str) -> None:
    """Fail closed before any resource mutation outside the dedicated namespace."""
    if not namespace.startswith(DEMO_PREFIX) or not spec.get("services"):
        raise ValueError("Missing demo namespace or services")
    for service in spec["services"].values():
        if (
            not service.get("container_name", "").startswith(namespace + "-")
            or service.get("labels", {}).get(DEMO_LABEL) != namespace
        ):
            raise ValueError("Compose service is not owned by this demo")
        if service.get("privileged") or service.get("network_mode") == "host":
            raise ValueError("Host networking and privileged services are not permitted")
        for port in service.get("ports", []):
            if not isinstance(port, dict) or port.get("host_ip") != "127.0.0.1":
                raise ValueError("Demo published ports must bind loopback")
    for kind in ("networks", "volumes"):
        for config in spec.get(kind, {}).values():
            if not config.get("name", "").startswith(namespace + "-"):
                raise ValueError("Compose resource is outside this demo")


def isolate(
    spec: dict[str, Any],
    namespace: str,
    *,
    web_service: str,
    web_port: int,
    shared_network: str | None = None,
) -> dict[str, Any]:
    """Retain internal aliases; remove public exports and host-observability mounts."""
    network = shared_network or f"{namespace}-network"
    spec["name"] = namespace + ("-application" if shared_network else "-signoz")
    spec["networks"] = {"demo": {"name": network, **({"external": True} if shared_network else {})}}
    for key in spec.get("volumes", {}):
        spec["volumes"][key] = {"name": f"{namespace}-{key}"}
    for name, service in spec["services"].items():
        original = service.get("container_name", name)
        service["container_name"] = (
            original if original.startswith(namespace + "-") else f"{namespace}-{name}"
        )
        service["labels"] = {**service.get("labels", {}), DEMO_LABEL: namespace}
        service["networks"] = {
            "demo": {"aliases": list(dict.fromkeys([name, original, service["container_name"]]))}
        }
        service["restart"] = service.get("restart", "unless-stopped")
        service.pop("build", None)
        service.pop("ports", None)
        if name == web_service:
            service["ports"] = [
                {
                    "target": 8080,
                    "published": str(web_port),
                    "host_ip": "127.0.0.1",
                    "protocol": "tcp",
                }
            ]
        for volume in service.get("volumes", []):
            if isinstance(volume, dict) and volume.get("type") == "bind":
                path = Path(volume.get("source", ""))
                if str(path) == "/" or "docker.sock" in str(path):
                    raise ValueError("Host telemetry mounts are not permitted in this demo")
        # Foundry's histogram helper downloads an executable at container startup.
        # This demo uses count(), so leave its volume empty instead.
        if name.endswith("-clickhouse-user-scripts"):
            service["command"] = ["/bin/true"]
            service["restart"] = "no"
    owned(spec, namespace)
    return spec


def freeze_images(spec: dict[str, Any], root: Path) -> dict[str, str]:
    """Resolve tagged upstream images to immutable registry digests before up."""
    pins: dict[str, str] = {}
    for service in spec["services"].values():
        image = service["image"]
        if image not in pins:
            if ":latest" in image or (
                ":" not in image.rsplit("/", 1)[-1] and "@sha256:" not in image
            ):
                raise ValueError("Every demo image must name a release tag or digest")
            run(["docker", "pull", image], root)
            data = json.loads(run(["docker", "image", "inspect", image], root))[0]
            digests = data.get("RepoDigests", [])
            if not digests:
                raise ValueError("Registry did not provide an immutable image digest")
            pins[image] = digests[0]
        service["image"] = pins[image]
    return pins


def generate(
    root: Path, binary: Path, application: Path, namespace: str, signoz_port: int, app_port: int
) -> tuple[list[Path], dict[str, str]]:
    """Use official Foundry and upstream application Compose, with isolation overrides."""
    casting = {
        "apiVersion": "v1alpha1",
        "kind": "Installation",
        "metadata": {"name": namespace},
        "spec": {
            "deployment": {"flavor": "compose", "mode": "docker"},
            "signoz": {
                "spec": {
                    "image": f"signoz/signoz:{DEMO_SIGNOZ_VERSION}",
                    "version": DEMO_SIGNOZ_VERSION,
                }
            },
            "ingester": {
                "spec": {
                    "image": f"signoz/signoz-otel-collector:{DEMO_COLLECTOR_VERSION}",
                    "version": DEMO_COLLECTOR_VERSION,
                }
            },
            "mcp": {"spec": {"enabled": False}},
        },
    }
    casting_path = root / "casting.yaml"
    casting_path.write_text(yaml.safe_dump(casting))
    run(
        [
            str(binary),
            "--no-ledger",
            "--no-updater",
            "forge",
            "-f",
            str(casting_path),
            "-p",
            str(root / "pours"),
        ],
        root,
    )
    signoz_path = root / "pours" / "deployment" / "compose.yaml"
    # Compose resolves bind mounts, env vars and list-form fields once. Mutate the
    # resolved model, not YAML text or user-owned Docker defaults.
    spec = json.loads(
        run(
            ["docker", "compose", "-f", str(signoz_path), "config", "--format", "json"],
            signoz_path.parent,
        )
    )
    signoz = isolate(spec, namespace, web_service=f"{namespace}-signoz-0", web_port=signoz_port)
    env_file = application / ".env"
    text = env_file.read_text()
    replacements = {
        "DEMO_VERSION": DEMO_OTEL_VERSION,
        "OTEL_COLLECTOR_HOST": f"{namespace}-ingester",
        "LOCUST_BROWSER_USER_WEIGHT": "0",
        "LOCUST_HTTP_USER_WEIGHT": "1",
        "LOCUST_USERS": "3",
    }
    env_file.write_text(
        "\n".join(
            f"{line.split('=', 1)[0]}={replacements[line.split('=', 1)[0]]}"
            if "=" in line and line.split("=", 1)[0] in replacements
            else line
            for line in text.splitlines()
        )
        + "\n"
    )
    path = application / "compose.yaml"
    spec = json.loads(
        run(["docker", "compose", "-f", str(path), "config", "--format", "json"], application)
    )
    for name in ("otel-collector", "telemetry-docs", "flagd-ui"):
        spec["services"].pop(name, None)
    for service in spec["services"].values():
        dependencies = service.get("depends_on", {})
        service["depends_on"] = {
            name: value for name, value in dependencies.items() if name in spec["services"]
        }
    app = isolate(
        spec,
        namespace,
        web_service="frontend-proxy",
        web_port=app_port,
        shared_network=f"{namespace}-network",
    )
    pins = freeze_images(signoz, root) | freeze_images(app, root)
    paths = [root / "signoz.compose.json", root / "application.compose.json"]
    for path, contents in zip(paths, (signoz, app), strict=True):
        path.write_text(json.dumps(contents, indent=2))
        path.chmod(0o600)
    return paths, pins


def compose(root: Path, namespace: str, path: Path, *args: str) -> str:
    """Always re-check ownership before up/down; never prune or remove orphans."""
    if path.resolve().parent != root.resolve():
        raise ValueError("Compose manifest is outside the owned demo directory")
    owned(json.loads(path.read_text()), namespace)
    return run(
        [
            "docker",
            "compose",
            "-p",
            namespace + ("-application" if path.name.startswith("application") else "-signoz"),
            "-f",
            str(path),
            *args,
        ],
        root,
    )
