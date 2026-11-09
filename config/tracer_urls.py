"""Tracer API base URL for the active environment."""

from config.constants.tracer import TRACER_BASE_URL_DEV, TRACER_BASE_URL_PROD
from config.environment import Environment, get_environment

__all__ = ("get_tracer_base_url",)


def get_tracer_base_url() -> str:
    """Get Tracer base URL for current environment."""
    return (
        TRACER_BASE_URL_PROD if get_environment() == Environment.PRODUCTION else TRACER_BASE_URL_DEV
    )
