"""CLI boundary wiring — observability and integration ports.

Lives in a leaf module so ``environment`` (imported by ``renderers`` and
``tracker`` for utility plumbing) does not import those modules back —
that would create a static import cycle. Entry points (``__main__``,
MCP, remote server) and tests call :func:`install_product_adapters`
from here.
"""

from __future__ import annotations


def install_harness_providers() -> None:
    """Register integrations/tools adapters into :mod:`infrastructure.harness_providers`.

    Harness composition root for the interactive shell and tests. Lives in
    ``surfaces`` (not ``tools``) because ``tools`` and ``integrations`` are
    sibling layers and must not import each other — see ``.importlinter.strict``.
    """
    from bootstrap.adapters import install_harness_adapters

    install_harness_adapters()


def install_product_adapters() -> None:
    """Wire product adapters into observability and integration ports.

    Call once from each process entry point (CLI, MCP, remote server).
    Idempotent — re-registers the same callables so calling it twice
    is a no-op.

    Wires:
    - debug_print: stderr default → Rich-aware CLI version
    - progress tracker: Noop default → Rich-backed CLI singleton (lazy)
    - remote integrations fetcher: empty default → Tracer Cloud adapter
    - harness ports: catalog/store, tool registry, GitHub scope
    """
    from infrastructure.harness_providers import RemoteIntegrationsProvider
    from infrastructure.observability.render.debug import set_debug_printer
    from infrastructure.observability.render.progress import set_progress_tracker_factory
    from integrations.tracer.integrations_adapter import (
        fetch_tracer_remote_integrations,
    )
    from surfaces.shared.terminal.output.environment import debug_print
    from surfaces.shared.terminal.output.tracker import get_tracker

    set_debug_printer(debug_print)
    set_progress_tracker_factory(get_tracker)
    RemoteIntegrationsProvider(fetch_tracer_remote_integrations).install()
    install_harness_providers()
