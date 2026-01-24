"""
Runtime configuration: load environment variables and validate required keys.

Import this module FIRST in entry points to ensure env vars are loaded
before any other imports that may depend on them.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def init_runtime() -> dict:
    """
    Initialize runtime configuration.
    
    Loads .env file and validates required environment variables.
    Must be called before importing modules that depend on env vars.
    
    Returns:
        dict with runtime config info (e.g., langsmith_enabled)
    """
    # Load .env from project root
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=env_path, override=True)
    
    # Verify API key is set
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not found in environment or .env file", file=sys.stderr)
        print(f"Please create a .env file at {env_path} with:", file=sys.stderr)
        print("ANTHROPIC_API_KEY=your_api_key_here", file=sys.stderr)
        sys.exit(1)
    
    # Check LangSmith configuration
    langsmith_enabled = bool(os.getenv("LANGSMITH_API_KEY"))
    langsmith_project = os.getenv("LANGSMITH_PROJECT")

    if langsmith_enabled:
        # Ensure tracing is enabled for LangChain/LangSmith
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    
    return {
        "langsmith_enabled": langsmith_enabled,
        "langsmith_project": langsmith_project,
        "env_path": str(env_path),
    }

