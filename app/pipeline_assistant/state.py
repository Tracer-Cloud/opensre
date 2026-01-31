"""State definition for the pipeline assistant."""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class PipelineAssistantState(TypedDict, total=False):
    """State for pipeline assistant with tool calling.

    Fields:
        messages: Conversation history (auto-merged via add_messages)
        org_id: Organization ID from JWT for data filtering
        user_id: User ID from JWT for audit
        user_email: User email from JWT
        user_name: User's full name from JWT
        organization_slug: Organization slug from JWT
        context: Accumulated context from tool calls
        route: Routing decision ("tracer_data" or "general")
    """

    messages: Annotated[list[BaseMessage], add_messages]
    org_id: str
    user_id: str
    user_email: str
    user_name: str
    organization_slug: str
    context: dict[str, list[dict[str, str | int | float | None]] | str | int]
    route: str


def make_initial_state(
    org_id: str,
    user_id: str = "",
    user_email: str = "",
    user_name: str = "",
    organization_slug: str = "",
    messages: list[BaseMessage] | None = None,
) -> PipelineAssistantState:
    """Create initial state with required fields."""
    return PipelineAssistantState(
        messages=messages or [],
        org_id=org_id,
        user_id=user_id,
        user_email=user_email,
        user_name=user_name,
        organization_slug=organization_slug,
        context={},
    )
