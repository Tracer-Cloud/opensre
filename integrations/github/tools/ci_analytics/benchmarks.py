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
MEASURED_ON = date(2026, 9, 11)


@dataclass(frozen=True)
class Benchmark:
    """One well-known repository's figures, as measured on :data:`MEASURED_ON`.

    ``label`` is the ``owner/repo`` slug; ``owner`` and ``repo`` are derived from it.
    """

    label: str
    figures: Mapping[str, str]

    @property
    def owner(self) -> str:
        return self.label.partition("/")[0]

    @property
    def repo(self) -> str:
        return self.label.partition("/")[2]


BENCHMARKS: tuple[Benchmark, ...] = (
    Benchmark(
        label="langchain-ai/langchain",
        figures=MappingProxyType(
            {
                "Red time on main": "7.5%",
                "Mean time to green": "9.0h",
                "CI-caused failure rate": "1.9%",
                "Slowest normal run": "4m",
                "PR failure rate": "11.5%",
            }
        ),
    ),
    Benchmark(
        label="anomalyco/opencode",
        figures=MappingProxyType(
            {
                "Red time on main": "31.6%",
                "Mean time to green": "4.3h",
                "CI-caused failure rate": "1.6%",
                "Slowest normal run": "20m",
                "PR failure rate": "54.6%",
            }
        ),
    ),
)


__all__ = ["BENCHMARKS", "MEASURED_ON", "WINDOW_DAYS", "Benchmark"]
