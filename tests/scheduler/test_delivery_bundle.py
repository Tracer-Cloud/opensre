"""The scheduled-delivery adapter bundle: assemble once, resolve by provider.

Pins the install-once / resolve semantics — in particular that installing a new
bundle atomically *replaces* the previous mapping rather than merging into it,
which is the property that made an immutable bundle preferable to a mutable
per-provider registry.
"""

from __future__ import annotations

import pytest

import infrastructure.scheduling.scheduler.delivery_bundle as delivery_bundle
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask


class _Adapter:
    def deliver(self, _task: ScheduledTask, _message: str) -> tuple[bool, str, str]:
        return True, "", "id"


@pytest.fixture(autouse=True)
def _reset_installed_bundle() -> None:
    # Reset on the way in as well as out: a bundle installed by an earlier test
    # module (booting a real process profile installs one) otherwise survives
    # into the "before any bundle is installed" case.
    delivery_bundle._installed = None
    yield
    delivery_bundle._installed = None


def test_resolve_returns_none_before_any_bundle_is_installed() -> None:
    assert delivery_bundle.resolve_delivery_adapter(Provider.TELEGRAM) is None


def test_installed_bundle_resolves_only_its_providers() -> None:
    adapter = _Adapter()

    delivery_bundle.ScheduledDeliveryAdapters({Provider.TELEGRAM: adapter}).install()

    assert delivery_bundle.resolve_delivery_adapter(Provider.TELEGRAM) is adapter
    assert delivery_bundle.resolve_delivery_adapter(Provider.SLACK) is None


def test_install_atomically_replaces_the_previous_bundle() -> None:
    first, second = _Adapter(), _Adapter()

    delivery_bundle.ScheduledDeliveryAdapters({Provider.TELEGRAM: first}).install()
    delivery_bundle.ScheduledDeliveryAdapters({Provider.SLACK: second}).install()

    # The whole mapping is replaced, not merged — no residue of the first bundle.
    assert delivery_bundle.resolve_delivery_adapter(Provider.TELEGRAM) is None
    assert delivery_bundle.resolve_delivery_adapter(Provider.SLACK) is second


def test_providers_reports_the_bundled_set() -> None:
    bundle = delivery_bundle.ScheduledDeliveryAdapters(
        {Provider.TELEGRAM: _Adapter(), Provider.SLACK: _Adapter()}
    )

    assert bundle.providers() == frozenset({Provider.TELEGRAM, Provider.SLACK})
