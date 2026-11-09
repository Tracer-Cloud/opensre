"""Tests for tool availability helpers."""

from __future__ import annotations

from integrations.bitbucket.tools.availability import bitbucket_available_or_backend
from integrations.cloudwatch.availability import cloudwatch_is_available
from integrations.datadog.availability import datadog_available_or_backend
from integrations.eks.availability import eks_available_or_backend


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

    def test_eks_check_ignores_bench_backend_in_dedicated_slot(self) -> None:
        """The bench adapter puts its replay backend at ``_bench_backend``,
        NOT ``_backend``. Production availability checks only look at
        ``_backend`` and ``connection_verified`` — they must stay completely
        unaware of bench backends. Regression-pin for the slot-separation
        refactor (no more ``is_cloudopsbench_backend`` marker checks)."""
        # Bench-style backend in its own slot — _backend remains None.
        sources = {"eks": {"_bench_backend": object()}}
        assert eks_available_or_backend(sources) is False

    def test_eks_check_uses_only_backend_and_verified_no_provider_specific_logic(
        self,
    ) -> None:
        """Belt-and-suspenders: the function's full decision MUST be a clean
        ``bool(connection_verified or _backend)``. No special-case branches,
        no marker attribute lookups, no provider-aware logic. Adding
        provider-specific branches here would re-couple production tool
        availability to bench backend types."""
        # Connection verified alone → True
        assert eks_available_or_backend({"eks": {"connection_verified": True}}) is True
        # Backend alone → True
        assert eks_available_or_backend({"eks": {"_backend": object()}}) is True
        # Both → True
        assert (
            eks_available_or_backend({"eks": {"connection_verified": True, "_backend": object()}})
            is True
        )
        # Neither → False
        assert eks_available_or_backend({"eks": {}}) is False
        # Extra unrelated slots (like _bench_backend) must not affect the result
        assert (
            eks_available_or_backend({"eks": {"_bench_backend": object(), "anything": True}})
            is False
        )


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


class TestBitbucketAvailableOrBackend:
    def test_bitbucket_missing(self) -> None:
        sources: dict[str, dict] = {}
        assert bitbucket_available_or_backend(sources) is False

    def test_bitbucket_empty(self) -> None:
        sources = {"bitbucket": {}}
        assert bitbucket_available_or_backend(sources) is False

    def test_bitbucket_verified_with_credentials(self) -> None:
        sources = {
            "bitbucket": {
                "connection_verified": True,
                "workspace": "acme",
                "username": "bb-user",
                "app_password": "bb-pass",
            }
        }
        assert bitbucket_available_or_backend(sources) is True

    def test_bitbucket_missing_credentials(self) -> None:
        sources = {"bitbucket": {"connection_verified": True, "workspace": "acme"}}
        assert bitbucket_available_or_backend(sources) is False

    def test_bitbucket_requires_connection_verification(self) -> None:
        sources = {
            "bitbucket": {
                "workspace": "acme",
                "username": "bb-user",
                "app_password": "bb-pass",
            }
        }
        assert bitbucket_available_or_backend(sources) is False

    def test_bitbucket_backend(self) -> None:
        sources = {"bitbucket": {"_backend": object()}}
        assert bitbucket_available_or_backend(sources) is True

    def test_bitbucket_backend_overrides_failed_verification(self) -> None:
        sources = {"bitbucket": {"connection_verified": False, "_backend": object()}}
        assert bitbucket_available_or_backend(sources) is True


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
