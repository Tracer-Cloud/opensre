"""The gateway hosts the scheduler in-process unless told to run it separately."""

from __future__ import annotations

import pytest

from gateway.core.lifecycle.controller import _gateway_hosts_scheduler


def test_gateway_hosts_scheduler_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_GATEWAY_HOST_SCHEDULER", raising=False)
    assert _gateway_hosts_scheduler() is True


@pytest.mark.parametrize("value", ["0", "false", "off", "no", "  FALSE  "])
def test_disabled_when_flag_is_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    # A dedicated MODE=scheduler service runs the loop; the gateway must not too.
    monkeypatch.setenv("OPENSRE_GATEWAY_HOST_SCHEDULER", value)
    assert _gateway_hosts_scheduler() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_enabled_for_any_truthy_value(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OPENSRE_GATEWAY_HOST_SCHEDULER", value)
    assert _gateway_hosts_scheduler() is True
