"""Registry of all available investigation actions."""

from app.tools.registered_tool import RegisteredTool
from app.tools.registry import get_registered_tools


def get_available_actions() -> list[RegisteredTool]:
    """Return investigation-surface tools discovered under ``app/tools/``."""
    return get_registered_tools("investigation")
