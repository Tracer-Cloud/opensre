"""Explicit local-stack selectors; this suite never starts or deletes infrastructure."""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("telemetry-evidence")
    group.addoption("--evidence-clickhouse-port", type=int, default=None)
    group.addoption("--evidence-otlp-endpoint", default=None)
