"""Pipeline assistant for debugging conversations.

All data access is scoped to the user's organization via JWT authentication.

Module structure:
- prompts.py: System prompts for the assistant
- state.py: State definitions for the graph
- tools.py: LangChain tools for Tracer data access
- graph.py: LangGraph pipeline definition with routing

Configuration (JWT and LLM) is in app.config.
JWT authentication is handled by app.auth.jwt_auth.
"""

from app.config import (
    CLERK_CONFIG_DEV,
    CLERK_CONFIG_PROD,
    ClerkConfig,
    Environment,
    get_clerk_config,
    get_environment,
)
from app.pipeline_assistant.graph import build_graph, pipeline_assistant
from app.auth.jwt_auth import (
    JWTClaims,
    JWTExpiredError,
    JWTInvalidIssuerError,
    JWTMissingClaimError,
    JWTVerificationError,
    verify_jwt_async,
)
from app.pipeline_assistant.prompts import (
    PIPELINE_ASSISTANT_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
)
from app.pipeline_assistant.state import PipelineAssistantState, make_initial_state
from app.pipeline_assistant.tools import get_pipeline_assistant_tools

__all__ = [
    # Graph
    "build_graph",
    "pipeline_assistant",
    # State
    "PipelineAssistantState",
    "make_initial_state",
    # Config
    "ClerkConfig",
    "Environment",
    "get_clerk_config",
    "get_environment",
    "CLERK_CONFIG_DEV",
    "CLERK_CONFIG_PROD",
    # JWT Auth
    "JWTClaims",
    "JWTVerificationError",
    "JWTExpiredError",
    "JWTInvalidIssuerError",
    "JWTMissingClaimError",
    "verify_jwt_async",
    # Prompts
    "PIPELINE_ASSISTANT_SYSTEM_PROMPT",
    "ROUTER_SYSTEM_PROMPT",
    # Tools
    "get_pipeline_assistant_tools",
]
