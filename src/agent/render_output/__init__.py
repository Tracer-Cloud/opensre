"""Presentation layer - UI rendering and report formatting."""

from src.agent.render_output.render import (
    console,
    render_agent_output,
    render_api_response,
    render_bullets,
    render_dot,
    render_generating_outputs,
    render_investigation_start,
    render_llm_thinking,
    render_newline,
    render_root_cause_complete,
    render_saved_file,
    render_step_header,
)
from src.agent.render_output.report import (
    ReportContext,
    format_problem_md,
    format_slack_message,
)

__all__ = [
    "console",
    "render_investigation_start",
    "render_step_header",
    "render_api_response",
    "render_llm_thinking",
    "render_dot",
    "render_newline",
    "render_bullets",
    "render_root_cause_complete",
    "render_generating_outputs",
    "render_agent_output",
    "render_saved_file",
    "ReportContext",
    "format_slack_message",
    "format_problem_md",
]

