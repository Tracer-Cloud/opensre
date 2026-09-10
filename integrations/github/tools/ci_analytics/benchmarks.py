"""Measured CI reliability figures for well-known repositories, shipped with the product.

A first run has no saved snapshots, so a comparison built from local snapshots
is empty exactly when it matters most. These figures were measured with this
tool over a 30-day window and travel with the release; the report names the day
they were taken so none of it reads as today's data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

WINDOW_DAYS = 30
MEASURED_ON = date(2026, 9, 9)


@dataclass(frozen=True)
class Benchmark:
    """One well-known repository's figures, as measured on :data:`MEASURED_ON`."""

    owner: str
    repo: str
    figures: Mapping[str, str]

    @property
    def label(self) -> str:
        return f"{self.owner}/{self.repo}"


BENCHMARKS: tuple[Benchmark, ...] = (
    Benchmark(
        owner="apache",
        repo="airflow",
        figures=MappingProxyType(
            {
                "Red time on main": "3.4%",
                "Mean time to green": "6.1h",
                "CI-caused failure rate": "4.3%",
                "Slowest normal run": "105m",
                "PR failure rate": "25.4%",
            }
        ),
    ),
    Benchmark(
        owner="fastapi",
        repo="fastapi",
        figures=MappingProxyType(
            {
                "Red time on main": "0.0%",
                "Mean time to green": "n/a",
                "CI-caused failure rate": "0.0%",
                "Slowest normal run": "5m",
                "PR failure rate": "19.1%",
            }
        ),
    ),
)


__all__ = ["BENCHMARKS", "MEASURED_ON", "WINDOW_DAYS", "Benchmark"]
