from __future__ import annotations

import re
from pathlib import Path
from typing import cast

from typing_extensions import TypedDict

from app.cli.tests.catalog import TestCatalog, TestCatalogItem, TestRequirement

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE_PATH = REPO_ROOT / "Makefile"
RCA_DIR = REPO_ROOT / "tests" / "e2e" / "rca"
SYNTHETIC_SCENARIOS_DIR = REPO_ROOT / "tests" / "synthetic" / "rds_postgres"

_TARGETS_TO_INDEX = (
    "test",
    "test-full",
    "test-cov",
    "test-grafana",
    "demo",
    "cloudwatch-demo",
    "datadog-demo",
    "crashloop-demo",
    "prefect-demo",
    "simulate-k8s-alert",
    "test-k8s-local",
    "test-k8s",
    "test-k8s-datadog",
    "test-k8s-eks",
    "trigger-alert",
    "trigger-alert-verify",
    "prefect-local-test",
    "upstream-downstream",
    "flink-demo",
    "grafana-demo",
    "deploy",
    "destroy",
    "deploy-lambda",
    "deploy-prefect",
    "deploy-flink",
    "destroy-lambda",
    "destroy-prefect",
    "destroy-flink",
    "deploy-dd-monitors",
    "cleanup-dd-monitors",
    "deploy-eks",
    "destroy-eks",
)


class _TargetMetadata(TypedDict, total=False):
    display_name: str
    tags: tuple[str, ...]
    requirements: TestRequirement


_TARGET_METADATA: dict[str, _TargetMetadata] = {
    "test": {
        "display_name": "Fast Unit + Prefect E2E",
        "tags": ("ci-safe", "test", "pytest"),
        "requirements": TestRequirement(),
    },
    "test-full": {
        "display_name": "Full Pytest Suite",
        "tags": ("ci-safe", "test", "pytest"),
        "requirements": TestRequirement(),
    },
    "test-cov": {
        "display_name": "Coverage Suite",
        "tags": ("ci-safe", "test", "coverage"),
        "requirements": TestRequirement(),
    },
    "test-grafana": {
        "display_name": "Grafana Integration Tests",
        "tags": ("test", "grafana"),
        "requirements": TestRequirement(env_vars=("ANTHROPIC_API_KEY", "OPENAI_API_KEY")),
    },
    "demo": {
        "display_name": "Prefect ECS Demo",
        "tags": ("demo", "aws"),
        "requirements": TestRequirement(notes=("AWS infra",)),
    },
    "cloudwatch-demo": {
        "display_name": "CloudWatch Demo",
        "tags": ("demo", "aws", "cloudwatch"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
    "datadog-demo": {
        "display_name": "Datadog Demo",
        "tags": ("demo", "datadog", "k8s", "infra-heavy"),
        "requirements": TestRequirement(
            env_vars=("DD_API_KEY", "DD_APP_KEY"), notes=("Docker/Kubernetes",)
        ),
    },
    "crashloop-demo": {
        "display_name": "CrashLoopBackOff Demo",
        "tags": ("demo", "datadog", "k8s"),
        "requirements": TestRequirement(env_vars=("DD_API_KEY", "DD_APP_KEY")),
    },
    "prefect-demo": {
        "display_name": "Prefect Demo Alias",
        "tags": ("demo", "aws"),
        "requirements": TestRequirement(notes=("AWS infra",)),
    },
    "simulate-k8s-alert": {
        "display_name": "Simulate Kubernetes Alert",
        "tags": ("k8s", "datadog", "infra-heavy"),
        "requirements": TestRequirement(notes=("LangGraph dev server", "Kubernetes context")),
    },
    "test-k8s-local": {
        "display_name": "Kubernetes Local Test",
        "tags": ("k8s", "ci-safe"),
        "requirements": TestRequirement(notes=("Local cluster",)),
    },
    "test-k8s": {
        "display_name": "Kubernetes Test",
        "tags": ("k8s", "infra-heavy"),
        "requirements": TestRequirement(notes=("Kubernetes test env",)),
    },
    "test-k8s-datadog": {
        "display_name": "Kubernetes + Datadog Test",
        "tags": ("k8s", "datadog", "infra-heavy"),
        "requirements": TestRequirement(env_vars=("DD_API_KEY", "DD_APP_KEY")),
    },
    "test-k8s-eks": {
        "display_name": "Kubernetes + Datadog On EKS",
        "tags": ("k8s", "aws", "datadog", "infra-heavy"),
        "requirements": TestRequirement(
            env_vars=("DD_API_KEY", "DD_APP_KEY"), notes=("EKS cluster",)
        ),
    },
    "trigger-alert": {
        "display_name": "Trigger K8s Alert",
        "tags": ("k8s", "datadog"),
        "requirements": TestRequirement(notes=("Kubernetes alert env",)),
    },
    "trigger-alert-verify": {
        "display_name": "Trigger K8s Alert + Verify",
        "tags": ("k8s", "datadog", "infra-heavy"),
        "requirements": TestRequirement(notes=("Slack + Datadog configured",)),
    },
    "prefect-local-test": {
        "display_name": "Prefect Local Test",
        "tags": ("aws", "local"),
        "requirements": TestRequirement(notes=("Optional CLOUD=1",)),
    },
    "upstream-downstream": {
        "display_name": "Upstream/Downstream Lambda E2E",
        "tags": ("aws", "demo"),
        "requirements": TestRequirement(notes=("AWS infra",)),
    },
    "flink-demo": {
        "display_name": "Apache Flink ECS Demo",
        "tags": ("aws", "demo"),
        "requirements": TestRequirement(notes=("AWS infra",)),
    },
    "grafana-demo": {
        "display_name": "Grafana Demo",
        "tags": ("demo", "grafana"),
        "requirements": TestRequirement(),
    },
    "deploy": {
        "display_name": "Deploy All Test Stacks",
        "tags": ("aws", "infra-heavy", "deploy"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
    "destroy": {
        "display_name": "Destroy All Test Stacks",
        "tags": ("aws", "infra-heavy", "destroy"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
    "deploy-lambda": {
        "display_name": "Deploy Lambda Stack",
        "tags": ("aws", "deploy"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
    "deploy-prefect": {
        "display_name": "Deploy Prefect Stack",
        "tags": ("aws", "deploy"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
    "deploy-flink": {
        "display_name": "Deploy Flink Stack",
        "tags": ("aws", "deploy"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
    "destroy-lambda": {
        "display_name": "Destroy Lambda Stack",
        "tags": ("aws", "destroy"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
    "destroy-prefect": {
        "display_name": "Destroy Prefect Stack",
        "tags": ("aws", "destroy"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
    "destroy-flink": {
        "display_name": "Destroy Flink Stack",
        "tags": ("aws", "destroy"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
    "deploy-dd-monitors": {
        "display_name": "Deploy Datadog Monitors",
        "tags": ("datadog", "deploy"),
        "requirements": TestRequirement(env_vars=("DD_API_KEY", "DD_APP_KEY")),
    },
    "cleanup-dd-monitors": {
        "display_name": "Cleanup Datadog Monitors",
        "tags": ("datadog", "destroy"),
        "requirements": TestRequirement(env_vars=("DD_API_KEY", "DD_APP_KEY")),
    },
    "deploy-eks": {
        "display_name": "Deploy EKS Test Cluster",
        "tags": ("aws", "k8s", "deploy", "infra-heavy"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
    "destroy-eks": {
        "display_name": "Destroy EKS Test Cluster",
        "tags": ("aws", "k8s", "destroy", "infra-heavy"),
        "requirements": TestRequirement(notes=("AWS credentials",)),
    },
}


def _comment_map_for_makefile(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    target_comments: dict[str, str] = {}
    comment_buffer: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            comment_buffer.append(stripped.lstrip("# ").strip())
            continue
        if not stripped:
            comment_buffer = []
            continue

        match = re.match(r"^([A-Za-z0-9_-]+):", stripped)
        if match:
            target_comments[match.group(1)] = " ".join(part for part in comment_buffer if part)
            comment_buffer = []
            continue

        comment_buffer = []

    return target_comments


def discover_make_targets() -> list[TestCatalogItem]:
    if not MAKEFILE_PATH.is_file():
        # PyInstaller-bundled builds ship only the ``app/`` tree (see
        # ``packaging/opensre.spec``), so the Makefile is absent at runtime.
        # Return an empty catalog slice so ``opensre tests`` still launches
        # against whatever sources *are* bundled.
        return []
    comment_map = _comment_map_for_makefile(MAKEFILE_PATH)
    makefile_text = MAKEFILE_PATH.read_text(encoding="utf-8")
    items: list[TestCatalogItem] = []

    for target in _TARGETS_TO_INDEX:
        if not re.search(rf"^{re.escape(target)}:", makefile_text, re.MULTILINE):
            continue
        metadata = _TARGET_METADATA.get(target, {})
        tags = cast(tuple[str, ...], metadata.get("tags") or ("make",))
        requirements = cast(TestRequirement, metadata.get("requirements") or TestRequirement())
        items.append(
            TestCatalogItem(
                id=f"make:{target}",
                kind="make_target",
                display_name=str(metadata.get("display_name") or target),
                description=comment_map.get(target) or f"Run `{target}` from the Makefile.",
                command=("make", target),
                tags=tags,
                source_path=str(MAKEFILE_PATH),
                requirements=requirements,
            )
        )

    return items


def discover_rca_files() -> list[TestCatalogItem]:
    items: list[TestCatalogItem] = []
    if not RCA_DIR.is_dir():
        # ``Path.glob`` on a missing parent returns an empty iterator on
        # CPython, so this isn't a crash today — but the explicit guard
        # documents the bundled-binary contract and matches the shape of
        # the other ``discover_*`` helpers below.
        return items
    for path in sorted(RCA_DIR.glob("*.md")):
        title = path.stem.replace("_", " ").title()
        first_line = path.read_text(encoding="utf-8").splitlines()[0].strip()
        if first_line.startswith("# "):
            title = first_line[2:].strip()
        items.append(
            TestCatalogItem(
                id=f"rca:{path.stem}",
                kind="rca_file",
                display_name=title,
                description="Run a bundled markdown RCA alert fixture.",
                command=("make", "test-rca", f"FILE={path.stem}"),
                tags=("rca", "fixture"),
                source_path=str(path),
                requirements=TestRequirement(env_vars=("ANTHROPIC_API_KEY", "OPENAI_API_KEY")),
            )
        )
    return items


def _discover_rds_synthetic_scenarios() -> list[TestCatalogItem]:
    """One catalog item per RDS synthetic scenario directory.

    Bundled (PyInstaller) builds collect only ``app/`` data files (see
    ``packaging/opensre.spec``), so ``tests/synthetic/rds_postgres`` is
    absent at runtime and ``iterdir()`` would raise ``FileNotFoundError``.
    Skip cleanly in that case — the synthetic-suite catalog entries are
    only meaningful when the scenarios are on disk anyway.
    """
    items: list[TestCatalogItem] = []
    if not SYNTHETIC_SCENARIOS_DIR.is_dir():
        return items
    req = TestRequirement(env_vars=("ANTHROPIC_API_KEY",))
    for scenario_dir in sorted(SYNTHETIC_SCENARIOS_DIR.iterdir()):
        if not scenario_dir.is_dir() or scenario_dir.name.startswith("_"):
            continue
        scenario_id = scenario_dir.name
        # Read display name from scenario.yml if present, else use directory name.
        display_name = scenario_id
        scenario_yml = scenario_dir / "scenario.yml"
        if scenario_yml.exists():
            try:
                import yaml  # type: ignore[import-untyped]

                meta = yaml.safe_load(scenario_yml.read_text()) or {}
                failure_mode = meta.get("failure_mode", "")
                if failure_mode:
                    display_name = f"{scenario_id}  [{failure_mode}]"
            except Exception:  # noqa: BLE001 — best-effort enrichment; malformed YAML is fine
                display_name = scenario_id
        items.append(
            TestCatalogItem(
                id=f"synthetic:{scenario_id}",
                kind="cli_command",
                display_name=display_name,
                description=f"Run the '{scenario_id}' synthetic RCA scenario against the mock backend.",
                command=("opensre", "tests", "synthetic", "--scenario", scenario_id),
                tags=("synthetic", "rds", "test"),
                source_path=str(scenario_dir),
                requirements=req,
            )
        )
    return items


def discover_cli_commands() -> list[TestCatalogItem]:
    """Catalog entries for opensre sub-commands that have no Makefile equivalent."""
    return _discover_rds_synthetic_scenarios()


def load_test_catalog() -> TestCatalog:
    items: list[TestCatalogItem] = []
    items.extend(discover_cli_commands())
    items.extend(discover_make_targets())
    items.extend(discover_rca_files())
    items.sort(key=lambda item: item.display_name.lower())
    return TestCatalog(items=tuple(items))
