"""Tests for tool availability helpers."""

from __future__ import annotations

from app.tools.utils.availability import (
    cloudwatch_is_available,
    datadog_available_or_backend,
    eks_available_or_backend,
)


class TestEksAvailableOrBackend:
    def test_eks_missing(self) -> None:
        sources: dict[str, dict] = {}
        assert eks_available_or_backend(sources) is False

    def test_eks_empty(self) -> None:
        sources = {"eks": {}}
        assert eks_available_or_backend(sources) is False

    def test_eks_verified(self) -> None:
        sources = {"eks": {"connection_verified": True}}
        assert eks_available_or_backend(sources) is True

    def test_eks_backend(self) -> None:
        sources = {"eks": {"_backend": object()}}
        assert eks_available_or_backend(sources) is True

    def test_eks_not_available(self) -> None:
        sources = {"eks": {"connection_verified": False}}
        assert eks_available_or_backend(sources) is False

    def test_eks_backend_none(self) -> None:
        sources = {"eks": {"_backend": None}}
        assert eks_available_or_backend(sources) is False

    def test_eks_backend_overrides_failed_verification(self) -> None:
        sources = {"eks": {"connection_verified": False, "_backend": object()}}
        assert eks_available_or_backend(sources) is True


class TestDatadogAvailableOrBackend:
    def test_datadog_missing(self) -> None:
        sources: dict[str, dict] = {}
        assert datadog_available_or_backend(sources) is False

    def test_datadog_empty(self) -> None:
        sources = {"datadog": {}}
        assert datadog_available_or_backend(sources) is False

    def test_datadog_verified(self) -> None:
        sources = {"datadog": {"connection_verified": True}}
        assert datadog_available_or_backend(sources) is True

    def test_datadog_backend(self) -> None:
        sources = {"datadog": {"_backend": object()}}
        assert datadog_available_or_backend(sources) is True

    def test_datadog_not_available(self) -> None:
        sources = {"datadog": {"connection_verified": False}}
        assert datadog_available_or_backend(sources) is False

    def test_datadog_backend_none(self) -> None:
        sources = {"datadog": {"_backend": None}}
        assert datadog_available_or_backend(sources) is False

    def test_datadog_backend_overrides_failed_verification(self) -> None:
        sources = {"datadog": {"connection_verified": False, "_backend": object()}}
        assert datadog_available_or_backend(sources) is True


class TestCloudwatchIsAvailable:
    def test_cloudwatch_missing(self) -> None:
        sources: dict[str, dict] = {}
        assert cloudwatch_is_available(sources) is False

    def test_cloudwatch_present_empty(self) -> None:
        sources = {"cloudwatch": {}}
        assert cloudwatch_is_available(sources) is False

    def test_cloudwatch_with_data(self) -> None:
        sources = {"cloudwatch": {"log_group": "test"}}
        assert cloudwatch_is_available(sources) is True
