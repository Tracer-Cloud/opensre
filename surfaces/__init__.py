"""User-facing surfaces — one folder per UI/client.

Each surface is a distinct way a human (or another system) interacts
with OpenSRE:

* :mod:`surfaces.cli` — stateless command runner (``opensre <command>``)
* :mod:`surfaces.interactive_shell` — stateful REPL surface (``opensre``)
* :mod:`surfaces.shared` — code two or more surfaces import
* :mod:`surfaces.entrypoint` — the ``opensre`` console script that composes
  the surfaces above; ``cli`` and ``interactive_shell`` never import each other

Slack lives elsewhere: the inbound transport in :mod:`gateway.transports.slack` (the
layering contract forbids ``gateway`` → ``surfaces`` imports), outbound
delivery in :mod:`integrations.slack`.

Layering rule: surfaces may import from ``core/``, ``tools/``,
``integrations/``, ``infrastructure/``. Nothing first-party may import from
``surfaces/`` (it sits at the top of the dependency stack).

See ``docs/ARCHITECTURE.md`` for the full layering contract and
restructure rationale.
"""

from __future__ import annotations
