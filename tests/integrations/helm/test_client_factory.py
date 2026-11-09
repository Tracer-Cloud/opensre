"""Tests for Helm client construction from raw tool params."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from integrations.config_models import HelmIntegrationConfig
from integrations.helm import client_factory
from integrations.helm.client import HelmClient


def _helm_validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as exc_info:
        HelmIntegrationConfig.model_validate({"unexpected_field": "value"})
    return exc_info.value


def test_helm_client_for_run_builds_client_for_valid_config() -> None:
    client = client_factory.helm_client_for_run(integration_id="helm-test")

    assert isinstance(client, HelmClient)


def test_helm_client_for_run_returns_none_for_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validate = MagicMock(side_effect=_helm_validation_error())
    debug_log = MagicMock()
    monkeypatch.setattr(client_factory.HelmIntegrationConfig, "model_validate", validate)
    monkeypatch.setattr(client_factory.logger, "debug", debug_log)

    assert client_factory.helm_client_for_run() is None
    debug_log.assert_not_called()


def test_helm_client_for_run_logs_unexpected_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError("unexpected validation failure")
    validate = MagicMock(side_effect=error)
    debug_log = MagicMock()
    monkeypatch.setattr(client_factory.HelmIntegrationConfig, "model_validate", validate)
    monkeypatch.setattr(client_factory.logger, "debug", debug_log)

    assert client_factory.helm_client_for_run() is None
    debug_log.assert_called_once_with(
        "helm_client_for_run failed unexpectedly",
        exc_info=True,
    )
