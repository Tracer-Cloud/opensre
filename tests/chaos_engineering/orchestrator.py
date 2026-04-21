"""Full chaos lab lifecycle: kind, Datadog, Chaos Mesh, baseline workloads."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.chaos_engineering import kubectl
from tests.chaos_engineering.paths import (
    BASE_MANIFESTS_APPLY_ORDER,
    BASE_MANIFESTS_DELETE_ORDER,
    CHAOS_MESH_NAMESPACE_DEFAULT,
    CLUSTER_NAME_DEFAULT,
    DATADOG_NAMESPACE_DEFAULT,
    chaos_engineering_path,
)
from tests.e2e.kubernetes.infrastructure_sdk.local import (
    check_prerequisites,
    cluster_exists,
    create_kind_cluster,
    delete_kind_cluster,
    deploy_datadog_helm,
    wait_for_datadog_agent,
)

CHAOS_MESH_HELM_REPO = "https://charts.chaos-mesh.org"
CHAOS_MESH_RELEASE = "chaos-mesh"
CHAOS_MESH_CHART = "chaos-mesh/chaos-mesh"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _datadog_values_path() -> str:
    return str(_repo_root() / "tests/e2e/kubernetes/k8s_manifests/datadog-values.yaml")


def install_chaos_mesh(*, kube_context: str | None, runtime: str = "containerd") -> None:
    kubectl.helm_repo_add("chaos-mesh", CHAOS_MESH_HELM_REPO)
    kubectl.kubectl_create_namespace(CHAOS_MESH_NAMESPACE_DEFAULT, context=kube_context)
    kubectl.helm_upgrade_install(
        release=CHAOS_MESH_RELEASE,
        chart=CHAOS_MESH_CHART,
        namespace=CHAOS_MESH_NAMESPACE_DEFAULT,
        extra_args=["--set", f"chaosDaemon.runtime={runtime}"],
        kube_context=kube_context,
    )


def uninstall_chaos_mesh(*, kube_context: str | None) -> None:
    kubectl.helm_uninstall(
        CHAOS_MESH_RELEASE,
        namespace=CHAOS_MESH_NAMESPACE_DEFAULT,
        kube_context=kube_context,
    )
    subprocess.run(
        [
            *kubectl.kubectl_base(kube_context),
            "delete",
            "namespace",
            CHAOS_MESH_NAMESPACE_DEFAULT,
            "--ignore-not-found",
        ],
        check=False,
    )


def apply_baseline_manifests(*, context: str | None) -> None:
    for rel in BASE_MANIFESTS_APPLY_ORDER:
        kubectl.kubectl_apply(chaos_engineering_path(*rel.split("/")), context=context)


def delete_baseline_manifests(*, context: str | None) -> None:
    for rel in BASE_MANIFESTS_DELETE_ORDER:
        kubectl.kubectl_delete(chaos_engineering_path(*rel.split("/")), context=context)


def lab_up(
    *,
    kube_context: str | None,
    skip_kind: bool = False,
    skip_datadog: bool = False,
    chaos_runtime: str = "containerd",
    wait_datadog_agent: bool = True,
) -> None:
    missing = check_prerequisites()
    if missing:
        raise OSError(f"Missing tools: {', '.join(missing)}")

    if not skip_kind:
        create_kind_cluster(CLUSTER_NAME_DEFAULT)

    if not skip_datadog:
        deploy_datadog_helm(
            _datadog_values_path(),
            DATADOG_NAMESPACE_DEFAULT,
            kube_context=kube_context,
        )
        if wait_datadog_agent:
            wait_for_datadog_agent(
                DATADOG_NAMESPACE_DEFAULT,
                kube_context=kube_context,
            )

    install_chaos_mesh(kube_context=kube_context, runtime=chaos_runtime)
    apply_baseline_manifests(context=kube_context)


def lab_down(
    *,
    kube_context: str | None,
    skip_kind: bool = False,
    skip_datadog: bool = False,
) -> None:
    delete_baseline_manifests(context=kube_context)
    uninstall_chaos_mesh(kube_context=kube_context)

    if not skip_datadog:
        kubectl.helm_uninstall(
            "datadog",
            namespace=DATADOG_NAMESPACE_DEFAULT,
            kube_context=kube_context,
        )
        subprocess.run(
            [
                *kubectl.kubectl_base(kube_context),
                "delete",
                "namespace",
                DATADOG_NAMESPACE_DEFAULT,
                "--ignore-not-found",
            ],
            check=False,
        )

    if not skip_kind and cluster_exists(CLUSTER_NAME_DEFAULT):
        delete_kind_cluster(CLUSTER_NAME_DEFAULT)
