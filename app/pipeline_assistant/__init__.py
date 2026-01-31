"""Pipeline assistant for debugging conversations.

All data access is scoped to the user's organization via JWT authentication.

Module structure:
- config.py: Configuration constants (Clerk domains, JWT settings)
- jwt_auth.py: Async JWT verification with JWKS
- prompts.py: System prompts for the assistant
- state.py: State definitions for the graph
- tools.py: LangChain tools for Tracer data access
- graph.py: LangGraph pipeline definition with routing
"""

from app.pipeline_assistant.config import (
    ClerkConfig,
    Environment,
    get_clerk_config,
    get_environment,
    CLERK_CONFIG_DEV,
    CLERK_CONFIG_PROD,
)
from app.pipeline_assistant.graph import build_graph, pipeline_assistant
from app.pipeline_assistant.jwt_auth import (
    JWTClaims,
    JWTVerificationError,
    JWTExpiredError,
    JWTInvalidIssuerError,
    JWTMissingClaimError,
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
