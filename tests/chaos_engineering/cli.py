"""Click CLI backing the Makefile chaos-* targets (``python -m tests.chaos_engineering``)."""

from __future__ import annotations

import os

import click

from tests.chaos_engineering import experiment_ops, orchestrator
from tests.chaos_engineering.paths import (
    KUBECTL_CONTEXT_DEFAULT,
    ExperimentNotFoundError,
    experiment_summary_line,
    list_experiment_names,
)


def _effective_context(kube_context: str | None) -> str | None:
    if kube_context:
        return kube_context
    env_ctx = os.environ.get("KUBECTL_CONTEXT", "").strip()
    if env_ctx:
        return env_ctx
    return KUBECTL_CONTEXT_DEFAULT


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """Chaos Mesh lab on kind. Prefer Make: ``make chaos-lab-up``, ``make chaos-experiment-up``, etc."""


@cli.group("lab")
def lab_group() -> None:
    """Create or tear down the full lab (cluster, Datadog, Chaos Mesh, baseline workloads)."""


@lab_group.command("up")
@click.option(
    "--context",
    "kube_context",
    default=None,
    help="kubectl / helm kube-context (default: $KUBECTL_CONTEXT or kind-tracer-k8s-test).",
)
@click.option("--skip-kind", is_flag=True, help="Do not create kind cluster (reuse existing).")
@click.option(
    "--skip-datadog",
    is_flag=True,
    help="Skip Datadog Helm install (no DD_API_KEY required for lab up).",
)
@click.option(
    "--chaos-runtime",
    default="containerd",
    show_default=True,
    help="Chaos Daemon runtime (use containerd for modern kind).",
)
@click.option(
    "--no-wait-datadog",
    is_flag=True,
    help="Do not wait for Datadog Agent ready (faster when skipping agent checks).",
)
def lab_up(
    kube_context: str | None,
    skip_kind: bool,
    skip_datadog: bool,
    chaos_runtime: str,
    no_wait_datadog: bool,
) -> None:
    """Provision kind (optional), Datadog (optional), Chaos Mesh, and baseline chaos workloads."""
    ctx = _effective_context(kube_context)
    try:
        orchestrator.lab_up(
            kube_context=ctx,
            skip_kind=skip_kind,
            skip_datadog=skip_datadog,
            chaos_runtime=chaos_runtime,
            wait_datadog_agent=not no_wait_datadog,
        )
    except OSError as exc:
        raise click.ClickException(str(exc)) from exc


@lab_group.command("down")
@click.option("--context", "kube_context", default=None, help="kubectl / helm kube-context.")
@click.option("--keep-kind", is_flag=True, help="Leave kind cluster running.")
@click.option(
    "--keep-datadog",
    is_flag=True,
    help="Do not uninstall Datadog or delete tracer-test namespace.",
)
def lab_down(
    kube_context: str | None,
    keep_kind: bool,
    keep_datadog: bool,
) -> None:
    """Remove baseline workloads, Chaos Mesh, Datadog (optional), and kind (optional)."""
    ctx = _effective_context(kube_context)
    orchestrator.lab_down(
        kube_context=ctx,
        skip_kind=keep_kind,
        skip_datadog=keep_datadog,
    )


@cli.group("experiment")
def experiment_group() -> None:
    """Apply or delete manifests for one experiment under experiments/<name>/."""


@experiment_group.command("list")
def experiment_list() -> None:
    """Print experiment directory names (YAML discoverable)."""
    for name in list_experiment_names():
        click.echo(experiment_summary_line(name))


@experiment_group.command("apply")
@click.argument("name")
@click.option("--context", "kube_context", default=None, help="kubectl context.")
def experiment_apply(name: str, kube_context: str | None) -> None:
    """Apply *-demo.yaml then *-chaos.yaml for experiments/<name>/."""
    ctx = _effective_context(kube_context)
    try:
        experiment_ops.apply_experiment(name, context=ctx)
    except ExperimentNotFoundError as exc:
        raise click.ClickException(
            f"{exc}\n"
            "Run 'make chaos-experiment-list' (or 'python -m tests.chaos_engineering experiment list')."
        ) from exc


@experiment_group.command("delete")
@click.argument("name")
@click.option("--context", "kube_context", default=None, help="kubectl context.")
def experiment_delete(name: str, kube_context: str | None) -> None:
    """Delete *-chaos.yaml then *-demo.yaml for experiments/<name>/."""
    ctx = _effective_context(kube_context)
    try:
        experiment_ops.delete_experiment(name, context=ctx)
    except ExperimentNotFoundError as exc:
        raise click.ClickException(
            f"{exc}\n"
            "Run 'make chaos-experiment-list' (or 'python -m tests.chaos_engineering experiment list')."
        ) from exc
