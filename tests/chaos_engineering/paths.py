"""Paths and experiment manifest ordering (YAML/JSON live in this package directory)."""

from __future__ import annotations

from pathlib import Path

# Kubernetes manifests and experiments/ tree sit next to this module (see README.md).
CHAOS_ENGINEERING_DIR: Path = Path(__file__).resolve().parent
EXPERIMENTS_DIR: Path = CHAOS_ENGINEERING_DIR / "experiments"

CLUSTER_NAME_DEFAULT = "tracer-k8s-test"
KUBECTL_CONTEXT_DEFAULT = f"kind-{CLUSTER_NAME_DEFAULT}"
DATADOG_NAMESPACE_DEFAULT = "tracer-test"
CHAOS_MESH_NAMESPACE_DEFAULT = "chaos-mesh"

BASE_MANIFESTS_APPLY_ORDER: tuple[str, ...] = (
    "chaos-demo.yaml",
    "experiments/crashloop/crashloop-demo.yaml",
    "pod-kill-demo.yaml",
)

BASE_MANIFESTS_DELETE_ORDER: tuple[str, ...] = (
    "pod-kill-demo.yaml",
    "experiments/crashloop/crashloop-demo.yaml",
    "chaos-demo.yaml",
)


def chaos_engineering_path(*parts: str) -> Path:
    return CHAOS_ENGINEERING_DIR.joinpath(*parts)


def list_experiment_names() -> list[str]:
    """Directory names under experiments/ that contain at least one YAML file."""
    if not EXPERIMENTS_DIR.is_dir():
        return []
    names: list[str] = []
    for p in sorted(EXPERIMENTS_DIR.iterdir()):
        if p.is_dir() and any(p.glob("*.yaml")):
            names.append(p.name)
    return names


class ExperimentNotFoundError(FileNotFoundError):
    """No experiment directory or no YAML under experiments/<name>."""


def experiment_demo_yaml_paths(name: str) -> list[Path]:
    exp = EXPERIMENTS_DIR / name
    if not exp.is_dir():
        raise ExperimentNotFoundError(f"No experiment directory: {name!r}")
    return sorted(exp.glob("*-demo.yaml"))


def experiment_chaos_yaml_paths(name: str) -> list[Path]:
    exp = EXPERIMENTS_DIR / name
    if not exp.is_dir():
        raise ExperimentNotFoundError(f"No experiment directory: {name!r}")
    return sorted(exp.glob("*-chaos.yaml"))


def validate_experiment(name: str) -> None:
    """Raise ExperimentNotFoundError if directory or YAML is missing."""
    demos = experiment_demo_yaml_paths(name)
    chaos = experiment_chaos_yaml_paths(name)
    if not demos or not chaos:
        raise ExperimentNotFoundError(f"No *-demo.yaml or *-chaos.yaml in experiment: {name!r}")


def experiment_has_manifests(name: str) -> bool:
    try:
        validate_experiment(name)
    except ExperimentNotFoundError:
        return False
    return True


def infer_chaos_kind(chaos_yaml: Path) -> str | None:
    """Best-effort parse of the first document's kind: field."""
    try:
        import yaml

        text = chaos_yaml.read_text(encoding="utf-8")
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind"):
                return str(doc["kind"])
    except Exception:  # noqa: BLE001
        return None
    return None


def experiment_summary_line(name: str) -> str:
    """One-line description for list output."""
    kinds: list[str] = []
    for p in experiment_chaos_yaml_paths(name):
        k = infer_chaos_kind(p)
        if k:
            kinds.append(k)
    kinds_str = ", ".join(kinds) if kinds else "—"
    return f"{name} ({kinds_str})"
