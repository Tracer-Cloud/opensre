"""Start an agent session from an embedding Python program.

``AgentSession.start`` deliberately boots nothing: ``core`` may not import
``bootstrap`` (package layering), so the process-boot step is passed in by the
caller. CLI, gateway and web already boot their own profile and call
``AgentSession`` directly.

An embedded host has no such boot, and without adapters ``resolve_integrations``
returns nothing — the agent then answers with zero integrations connected. This
module is the one line that supplies it, and it lives here because ``bootstrap``
is the tier allowed to import both sides.

    from bootstrap.embedded import start_embedded_session

    session = start_embedded_session()
    result = session.chat("why did checkout fail?")
"""

from __future__ import annotations

from collections.abc import Callable

from bootstrap.process import EMBEDDED_PROFILE, configure_process
from core.agent_harness import AgentSession, OutputSink, SessionConfig, SessionCore
from core.agent_harness.ports import PromptContextProvider


def embedded_boot_step() -> None:
    """Install the embedded harness adapters. Idempotent per profile."""
    configure_process(EMBEDDED_PROFILE)


def start_embedded_session(
    config: SessionConfig | None = None,
    *,
    output: OutputSink | None = None,
    prompts: PromptContextProvider | None = None,
    prepare_session: Callable[[SessionCore], None] | None = None,
) -> AgentSession:
    """Return a started :class:`AgentSession` with local adapters installed.

    ``config.boot_process`` is filled in when the caller left it unset; a
    caller that supplies its own boot step keeps it.
    """
    from dataclasses import replace

    cfg = config or SessionConfig()
    if cfg.boot_process is None:
        cfg = replace(cfg, boot_process=embedded_boot_step)
    return AgentSession.start(cfg, output=output, prompts=prompts, prepare_session=prepare_session)


__all__ = ["embedded_boot_step", "start_embedded_session"]
