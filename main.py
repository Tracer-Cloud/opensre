"""OpenSRE process entrypoint.

Prefer the ``opensre`` console script in normal use. This module exists so
``python main.py`` and ``python -m`` discovery reach the same CLI as
``surfaces.entrypoint:main``.

This covers the interactive shell, the landing page, and every one-shot
subcommand. The gateway daemon is a separate process entry managed via
``opensre gateway start`` (CLI wires slash ports). Bare ``python -m gateway``
fails closed — ``gateway`` and ``surfaces`` are peer packages that must not
import each other.

Shared process setup — environment, error reporting, adapter registration —
lives in ``bootstrap.process``. Each host picks a profile rather than repeating
the order: this CLI uses ``CLI_PROFILE`` (via ``surfaces.cli.startup``), the
gateway ``GATEWAY_PROFILE``, the web app ``WEB_PROFILE``.

Driving the agent from Python instead of the CLI::

    from bootstrap.process import EMBEDDED_PROFILE, configure_process
    from core.agent_harness import AgentSession

    configure_process(EMBEDDED_PROFILE)

    session = AgentSession.start()  # one agent for this logical session
    result = session.chat("why is checkout-api slow?")
    if result.answered:
        print(result.primary_response_text)
    follow = session.chat("what should we check next?")  # same agent, next turn

``configure_process`` comes first and is what registers the harness adapters
tools resolve through; ``AgentSession.start()`` only loads the environment, so
on its own the agent starts but no tool is available. It cannot self-bootstrap —
``core`` sits below ``bootstrap`` in the layer contract, so the caller wires it.
``EMBEDDED_PROFILE`` deliberately leaves Sentry and LLM preloading to the host
process.

``start()`` opens a session and attaches an agent with the standard ports. Check
``result.answered`` before trusting the text — on a failed turn (e.g. the LLM
provider is unreachable) the error message itself lands in
``primary_response_text``. Surfaces that need their own ports — a live gateway
sink, a REPL console — build a ``HeadlessAgent`` (or
``DefaultHeadlessBuild(...).agent(...)``) and call ``attach_agent``, then ``chat``.

Construct **one** agent per logical session (or scheduled loop), then many
``chat`` turns — do not rebuild every message. Gateway does
this via ``SessionAgentPool`` + ``bind_turn``. There is no
``dispatch_message_to_headless_agent`` free function, and no ``AgentHarness``
alias — ``AgentSession`` is the only name.
"""

from __future__ import annotations


def main() -> int:
    """Run the CLI and return its exit status."""
    from surfaces.entrypoint import main as entrypoint_main

    return entrypoint_main()


if __name__ == "__main__":
    raise SystemExit(main())
