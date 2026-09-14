"""Wire the harness provider ports around every test, in every test tree.

Loaded through ``pytest.ini`` (``-p tests.harness_providers_plugin``) rather
than a conftest because conftest fixtures only reach tests below their own
directory. Skill workflow tests live beside their ``SKILL.md`` under
``core/agent_harness/prompts/skills`` and may not import ``surfaces``,
``tools``, or ``integrations`` themselves (layer contracts), so the wiring
has to come from the ``tests`` package.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _harness_providers_per_test() -> Iterator[None]:
    """Wire harness ports before each test; reset after to avoid session leakage."""
    from bootstrap.adapters import install_cli_auth_checker
    from infrastructure.harness_providers import reset_harness_providers
    from surfaces.shared.terminal.output.boundary import install_harness_providers

    install_harness_providers()
    install_cli_auth_checker()
    yield
    reset_harness_providers()
