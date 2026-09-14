"""Turn Langfuse tracing on for this process when the operator opted in.

Opt-in is ``LANGFUSE_PUBLIC_KEY`` + ``LANGFUSE_SECRET_KEY`` in the environment
and the ``langfuse`` extra installed. Anything else leaves the noop
observation sink in place, so contributors without a Langfuse project run
the same code path with zero overhead.
"""

from __future__ import annotations

import logging

from infrastructure.observability.langfuse.settings import resolve_langfuse_settings
from infrastructure.observability.trace.observations import (
    is_observation_sink_active,
    set_observation_sink,
)

log = logging.getLogger(__name__)

_MISSING_PACKAGE_HINT = (
    "Langfuse keys are set but the `langfuse` package is not installed; LLM tracing "
    "stays off. Install it with `uv sync --extra langfuse` or "
    "`pip install 'opensre[langfuse]'`."
)


def init_langfuse_tracing() -> bool:
    """Install the Langfuse observation sink; return whether tracing is on.

    Idempotent: a process that already has a sink keeps it. Never raises — a
    misconfigured tracing backend must not stop the agent from booting.
    """
    if is_observation_sink_active():
        return True
    settings = resolve_langfuse_settings()
    if settings is None:
        return False
    try:
        from langfuse import Langfuse
    except ImportError:
        log.warning(_MISSING_PACKAGE_HINT)
        return False

    from config.environment import get_environment
    from config.version import get_opensre_version
    from infrastructure.observability.langfuse.masking import mask_otel_spans
    from infrastructure.observability.langfuse.sink import LangfuseObservationSink

    try:
        client = Langfuse(
            public_key=settings.public_key,
            secret_key=settings.secret_key,
            base_url=settings.base_url,
            environment=get_environment().value,
            release=f"opensre@{get_opensre_version()}",
            mask_otel_spans=mask_otel_spans,
        )
    except Exception:  # noqa: BLE001 - boot must survive a broken tracing backend
        log.warning("Langfuse client could not be created; LLM tracing stays off", exc_info=True)
        return False
    set_observation_sink(LangfuseObservationSink(client))
    log.info("Langfuse LLM tracing enabled (%s)", settings.base_url)
    return True


__all__ = ["init_langfuse_tracing"]
