"""OpenSRE process entrypoint.

Prefer the ``opensre`` console script. ``python main.py`` and
``python -m surfaces`` call the same :func:`surfaces.entrypoint.main`.
``python -m surfaces.cli`` runs the command surface alone, without the shell.

A bare ``opensre`` on a terminal opens the interactive shell; otherwise it
prints the landing page. ``opensre <command>`` runs that one-shot command.
``opensre gateway start`` is the production gateway entry. This composition
root supplies the slash ports, and the daemon child is
``python -m surfaces.gateway_entry`` (or ``opensre gateway start --foreground``
when this process is the ``opensre`` launcher or a frozen binary). Bare
``python -m gateway`` exits: that module has no slash-port glue, and
``gateway`` must not import ``surfaces``.

Profiles and step order live in :mod:`infrastructure.process.boot`. The host
that may import both ``tools`` and ``integrations`` runs them:
``surfaces.shared.process_boot.configure_process`` for the CLI, shell, and
embedded sessions, and ``gateway.core.process.boot.configure_process`` for
the gateway and web app. This CLI passes ``CLI_PROFILE`` from
``surfaces.cli.startup`` (environment and the skills pull); Sentry and the
Rich product adapters stay in that module. The gateway passes
``GATEWAY_PROFILE``. The web app passes ``WEB_PROFILE``.

Driving the agent from Python::

    from surfaces.embedded import start_embedded_session

    session = start_embedded_session()  # EMBEDDED_PROFILE, then one agent
    result = session.chat("why is checkout-api slow?")
    follow = session.chat("what should we check next?")  # same agent, next turn

``start_embedded_session`` runs
``surfaces.shared.process_boot.configure_process(EMBEDDED_PROFILE)`` and then
``AgentSession.start``. ``start`` opens a session and attaches an agent with
the standard ports. ``core`` may not import the hosts above it, so the caller
registers adapters first; without that boot the agent starts and no tool is
available. ``EMBEDDED_PROFILE`` loads the environment and the harness
adapters, and leaves error reporting, LLM client preloading, and scheduling
to the host.

``answered`` means ``primary_response_text`` is non-empty. A failed model
call still fills that text, with ``action_result.accounting_status``
``"not_run"`` and ``stop_reason`` ``"error"``. ``cancelled`` is a host stop.
A caller that needs its own sink builds
``DefaultHeadlessBuild(...).agent(...)``, then calls ``attach_agent`` and
``chat``. The gateway and the interactive shell share ``TurnRunner`` and
``SessionAgentPool``: one ``HeadlessAgent`` per session, ``bind_turn``, then
``handle``.

Construct one agent per logical session (or scheduled loop), then many turns.
``AgentSession`` is the host API (``start`` / ``chat``).
"""

from __future__ import annotations


def main() -> int:
    """Run the CLI and return its exit status."""
    from surfaces.entrypoint import main as entrypoint_main

    return entrypoint_main()


if __name__ == "__main__":
    raise SystemExit(main())
