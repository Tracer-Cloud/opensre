"""Frame the problem and enrich context."""

import json

from pydantic import BaseModel, Field

from src.agent.state import InvestigationState
from src.agent.tools.llm import stream_completion


class ProblemStatement(BaseModel):
    """Structured problem statement for the investigation."""

    summary: str = Field(description="One-line summary of the problem")
    context: str = Field(description="Background context about the alert and affected systems")
    investigation_goals: list[str] = Field(description="Specific goals for the investigation")
    constraints: list[str] = Field(description="Known constraints or limitations")


def _build_prompt(state: InvestigationState) -> str:
    """Build the prompt for generating a problem statement."""
    schema_hint = {
        "summary": "one-line summary of the incident",
        "context": "2-3 sentences about what this alert means and why it matters",
        "investigation_goals": ["goal 1", "goal 2", "goal 3"],
        "constraints": ["constraint 1"],
    }

    return f"""You are framing a data pipeline incident for investigation.

Alert Information:
- alert_name: {state.get("alert_name", "Unknown")}
- affected_table: {state.get("affected_table", "Unknown")}
- severity: {state.get("severity", "Unknown")}

Task:
Return a single JSON object that matches the ProblemStatement schema exactly.

Rules:
- Output MUST be valid JSON.
- Output MUST be ONLY the JSON object (no markdown, no code fences, no commentary).
- investigation_goals and constraints MUST be JSON arrays of strings.

JSON schema (example shape, not literal values):
{json.dumps(schema_hint, indent=2)}
"""


def _extract_json_object(text: str) -> str:
    """Extract the first top-level JSON object from a model response."""
    raw = text.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in response")
    return raw[start : end + 1]


def _parse_problem_statement_json(response: str) -> ProblemStatement:
    """Parse model response as JSON and validate against ProblemStatement."""
    try:
        json_text = _extract_json_object(response)
        return ProblemStatement.model_validate_json(json_text)
    except Exception:
        # Safe fallback: keep the graph resilient if the model returns malformed output.
        return ProblemStatement(
            summary="Investigation required for data pipeline alert",
            context="Alert triggered for affected table",
            investigation_goals=["Identify root cause", "Assess impact", "Determine resolution"],
            constraints=["Limited to available evidence sources"],
        )


def _render_problem_statement_md(problem: ProblemStatement, state: InvestigationState) -> str:
    goals_md = "\n".join(f"- {goal}" for goal in problem.investigation_goals)
    constraints_md = "\n".join(f"- {constraint}" for constraint in problem.constraints)

    return f"""# Problem Statement

## Summary
{problem.summary}

## Context
{problem.context}

## Investigation Goals
{goals_md}

## Constraints
{constraints_md}

## Alert Details
- **Alert**: {state.get("alert_name", "Unknown")}
- **Table**: {state.get("affected_table", "Unknown")}
- **Severity**: {state.get("severity", "Unknown")}

## Next Steps
Proceed to gather evidence from relevant sources."""


def node_frame_problem(state: InvestigationState) -> dict:
    """
    Enrich the initial alert with investigation context using LLM.

    Uses Pydantic for output validation. Visible in LangSmith via LangChain tracing.

    Returns:
        dict with problem_md (str) containing the formatted problem statement
    """
    prompt = _build_prompt(state)
    response = stream_completion(prompt)

    problem = _parse_problem_statement_json(response)
    return {"problem_md": _render_problem_statement_md(problem, state)}
