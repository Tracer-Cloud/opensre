"""Verify the documented telemetry kill-switch matrix.

The README and `init_sentry` docstring promise:

| Env var                          | PostHog  | Sentry   |
| -------------------------------- | -------- | -------- |
| `OPENSRE_NO_TELEMETRY=1`         | disabled | disabled |
| `DO_NOT_TRACK=1`                 | disabled | disabled |
| `OPENSRE_ANALYTICS_DISABLED=1`   | disabled | enabled  |
| `OPENSRE_SENTRY_DISABLED=1`      | enabled  | disabled |

These tests pin the table so a future refactor cannot silently re-route or
drop one of the flags.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from infrastructure.analytics import provider
from infrastructure.observability.errors import sentry as sentry_mod

_ENV_VARS = (
    "OPENSRE_NO_TELEMETRY",
    "DO_NOT_TRACK",
    "OPENSRE_ANALYTICS_DISABLED",
    "OPENSRE_SENTRY_DISABLED",
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    sentry_mod._init_sentry_once.cache_clear()


def _posthog_is_disabled() -> bool:
    return provider.analytics_opted_out()


def _sentry_init_call_count(monkeypatch: pytest.MonkeyPatch) -> int:
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))
    # Stubbed sentry_sdk module is not a package, so the real integrations
    # submodule import would fail; bypass it for this counting test.
    monkeypatch.setattr(sentry_mod, "_build_sentry_integrations", lambda: [])
    sentry_mod._init_sentry_once.cache_clear()
    sentry_mod.init_sentry()
    return init_mock.call_count


@pytest.mark.parametrize(
    ("env_var", "posthog_disabled", "sentry_disabled"),
    [
        ("OPENSRE_NO_TELEMETRY", True, True),
        ("DO_NOT_TRACK", True, True),
        ("OPENSRE_ANALYTICS_DISABLED", True, False),
        ("OPENSRE_SENTRY_DISABLED", False, True),
    ],
)
def test_kill_switch_matrix(
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
    posthog_disabled: bool,
    sentry_disabled: bool,
) -> None:
    monkeypatch.setenv(env_var, "1")

    assert _posthog_is_disabled() is posthog_disabled

    init_calls = _sentry_init_call_count(monkeypatch)
    if sentry_disabled:
        assert init_calls == 0
    else:
        assert init_calls == 1


def test_baseline_with_no_env_vars_enables_both(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _posthog_is_disabled() is False
    assert _sentry_init_call_count(monkeypatch) == 1
