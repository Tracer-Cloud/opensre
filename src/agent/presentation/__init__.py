"""Presentation layer - UI rendering and report formatting."""

from src.agent.presentation.render import (
    console,
    render_investigation_start,
    render_step_header,
    render_api_response,
    render_llm_thinking,
    render_dot,
    render_newline,
    render_bullets,
    render_root_cause_complete,
    render_generating_outputs,
    render_agent_output,
    render_saved_file,
)
from src.agent.presentation.report import (
    ReportContext,
    format_slack_message,
    format_problem_md,
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

