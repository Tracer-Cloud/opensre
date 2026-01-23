"""
Runtime configuration for the incident resolution agent.

Handles:
- .env loading from project root
- Anthropic API key validation (fail fast with clear error)
- LangSmith tracing enablement

Usage:
    from config.config import init_runtime
    init_runtime()
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def _load_env() -> Path:
    """Load .env from project root and return the path."""
    # Find project root (where .env should be)
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=env_path, override=True)
    return env_path


def _validate_anthropic_key(env_path: Path) -> None:
    """Validate that Anthropic API key is set, fail fast if not."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not found in environment or .env file", file=sys.stderr)
        print(f"Please create a .env file at {env_path} with:", file=sys.stderr)
        print("ANTHROPIC_API_KEY=your_api_key_here", file=sys.stderr)
        sys.exit(1)


def _configure_langsmith() -> bool:
    """
    Configure LangSmith tracing if API key is available.
    
    LangSmith is automatically enabled when these environment variables are set:
      LANGSMITH_TRACING=true
      LANGSMITH_API_KEY=<your-langsmith-api-key>
      LANGSMITH_PROJECT=<your-project-name>  (optional, defaults to "default")
    
    The langchain library will automatically trace all LLM calls when enabled.
    
    Returns:
        True if LangSmith is enabled, False otherwise.
    """
    if os.getenv("LANGSMITH_API_KEY"):
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_PROJECT", "demo-jpm-2026")
        return True
    return False


# Module-level state
_initialized = False
_langsmith_enabled = False


def init_runtime() -> dict:
    """
    Initialize runtime configuration.
    
    This function should be called once at application startup, before any
    other imports that depend on environment variables.
    
    Returns:
        dict with configuration state:
            - langsmith_enabled: bool
            - langsmith_project: str | None
    """
    global _initialized, _langsmith_enabled
    
    if _initialized:
        return {
            "langsmith_enabled": _langsmith_enabled,
            "langsmith_project": os.getenv("LANGSMITH_PROJECT") if _langsmith_enabled else None,
        }
    
    # 1. Load .env from project root
    env_path = _load_env()
    
    # 2. Validate required keys (fail fast)
    _validate_anthropic_key(env_path)
    
    # 3. Configure optional services
    _langsmith_enabled = _configure_langsmith()
    
    _initialized = True
    
    return {
        "langsmith_enabled": _langsmith_enabled,
        "langsmith_project": os.getenv("LANGSMITH_PROJECT") if _langsmith_enabled else None,
    }


def is_langsmith_enabled() -> bool:
    """Check if LangSmith tracing is enabled."""
    return _langsmith_enabled

