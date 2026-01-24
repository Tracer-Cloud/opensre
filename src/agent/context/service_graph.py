"""Service graph context used by the agent."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceNode:
    """Represents a tool-backed evidence source."""

    name: str
    description: str
    tools: tuple[str, ...]


SERVICE_GRAPH: tuple[ServiceNode, ...] = (
    ServiceNode(
        name="Tracer pipeline",
        description="Run status, tasks, metrics, logs",
        tools=("get_tracer_run", "get_tracer_tasks", "get_batch_jobs"),
    ),
    ServiceNode(
        name="Tracer web app",
        description="Pipeline catalog and recent runs",
        tools=("get_pipelines", "get_pipeline_runs"),
    ),
    ServiceNode(
        name="AWS Batch",
        description="Job status and failure reasons",
        tools=("get_batch_jobs",),
    ),
)


def render_tools_briefing() -> str:
    """Render a human-readable tools briefing from the service graph."""
    lines = ["Available evidence sources:"]
    for node in SERVICE_GRAPH:
        lines.append(f"- {node.name}: {node.description}")
    return "\n".join(lines)
