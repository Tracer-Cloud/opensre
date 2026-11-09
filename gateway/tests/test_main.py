"""Tests for the package guard ``python -m gateway`` (``gateway/__main__.py``)."""

from __future__ import annotations

import pytest


def test_gateway_main_fails_closed_without_slash_ports() -> None:
    """Bare ``python -m gateway`` must not boot a slash-less gateway."""
    import gateway.__main__ as entry

    with pytest.raises(SystemExit, match="slash-port glue"):
        entry.main()


def test_manager_main_fails_closed_without_slash_ports() -> None:
    from gateway.core.lifecycle import controller as manager_module

    with pytest.raises(SystemExit, match="slash_ports_factory"):
        manager_module.main()


def test_manager_start_gateway_wrapper_requires_slash_ports() -> None:
    from gateway.core.lifecycle.controller import start_gateway

    with pytest.raises(SystemExit, match="slash_ports_factory"):
        start_gateway(wait=False)


def test_gateway_main_module_exports_main() -> None:
    import gateway.__main__ as entry

    assert callable(entry.main)
    assert entry.main.__module__ == "gateway.__main__"
