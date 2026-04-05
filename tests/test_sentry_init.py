from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.utils import sentry_sdk as sentry_mod


def test_init_sentry_noops_without_dsn(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()

    init_mock.assert_not_called()


def test_init_sentry_is_idempotent_for_same_config(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.setenv("SENTRY_DSN", "https://example@sentry.invalid/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.5")
    monkeypatch.setenv("ENV", "production")
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()
    sentry_mod.init_sentry()

    init_mock.assert_called_once()
    assert init_mock.call_args.kwargs["dsn"] == "https://example@sentry.invalid/1"
    assert init_mock.call_args.kwargs["environment"] == "production"
    assert init_mock.call_args.kwargs["traces_sample_rate"] == 0.5
