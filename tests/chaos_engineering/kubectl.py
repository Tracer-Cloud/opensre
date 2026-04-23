"""kubectl / helm subprocess helpers with optional context."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path


def kubectl_base(context: str | None) -> list[str]:
    cmd = ["kubectl"]
    if context:
        cmd.extend(["--context", context])
    return cmd


def kubectl_apply(manifest: Path | str, *, context: str | None) -> None:
    path = Path(manifest) if isinstance(manifest, str) else manifest
    subprocess.run(
        [*kubectl_base(context), "apply", "-f", str(path)],
        check=True,
    )


def kubectl_delete(manifest: Path | str, *, context: str | None) -> None:
    path = Path(manifest) if isinstance(manifest, str) else manifest
    subprocess.run(
        [*kubectl_base(context), "delete", "-f", str(path), "--ignore-not-found"],
        check=False,
    )


def kubectl_create_namespace(name: str, *, context: str | None) -> None:
    p_create = subprocess.run(
        [
            *kubectl_base(context),
            "create",
            "namespace",
            name,
            "--dry-run=client",
            "-o",
            "yaml",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [*kubectl_base(context), "apply", "-f", "-"],
        check=True,
        input=p_create.stdout,
    )


def helm_upgrade_install(
    *,
    release: str,
    chart: str,
    namespace: str,
    extra_args: Sequence[str],
    kube_context: str | None,
) -> None:
    cmd = [
        "helm",
        "upgrade",
        "--install",
        release,
        chart,
        "-n",
        namespace,
        *extra_args,
    ]
    if kube_context:
        cmd.extend(["--kube-context", kube_context])
    subprocess.run(cmd, check=True)


def helm_uninstall(release: str, *, namespace: str, kube_context: str | None) -> None:
    cmd = ["helm", "uninstall", release, "-n", namespace]
    if kube_context:
        cmd.extend(["--kube-context", kube_context])
    subprocess.run(cmd, check=False)


def helm_repo_add(name: str, url: str) -> None:
    subprocess.run(["helm", "repo", "add", name, url], check=False)
    subprocess.run(["helm", "repo", "update"], check=True)
