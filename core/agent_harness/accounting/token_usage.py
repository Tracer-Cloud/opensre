"""Per-session token accounting, extracted from the session state object.

Groups the token-cost bookkeeping (``/cost``) into one cohesive value object so
the session state class does not carry accounting fields and methods directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenUsage:
    """Accumulated token counts and LLM call count for one session.

    ``totals`` keeps running sums under ``input`` / ``output`` plus the
    ``*_measured`` / ``*_estimated`` breakdown buckets. ``call_count`` is the
    number of recorded LLM calls (for ``/cost``).
    """

    totals: dict[str, int] = field(default_factory=dict)
    call_count: int = 0

    def io_totals(self) -> tuple[int, int]:
        """Running ``(input, output)`` totals, coerced to non-negative ints."""
        try:
            return (
                max(0, int(self.totals.get("input", 0) or 0)),
                max(0, int(self.totals.get("output", 0) or 0)),
            )
        except (TypeError, ValueError):
            return 0, 0

    @property
    def has_estimates(self) -> bool:
        """True when any recorded tokens were estimated rather than measured."""
        return bool(self.totals.get("input_estimated") or self.totals.get("output_estimated"))

    def cached_total(self) -> int:
        """Input tokens the provider served from its prompt cache (part of ``input``)."""
        try:
            return max(0, int(self.totals.get("input_cached", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def record(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated: bool = False,
        cache_read_tokens: int = 0,
    ) -> None:
        """Accumulate one LLM call's token counts (input/output + breakdown).

        ``cache_read_tokens`` is the cached part of ``input_tokens``, kept as
        its own bucket so spend can be shown as cached versus fresh.
        """
        if not input_tokens and not output_tokens:
            return
        suffix = "estimated" if estimated else "measured"
        for direction, count in (("input", input_tokens), ("output", output_tokens)):
            if not count:
                continue
            self.totals[direction] = self.totals.get(direction, 0) + count
            bucket = f"{direction}_{suffix}"
            self.totals[bucket] = self.totals.get(bucket, 0) + count
        if cache_read_tokens:
            self.totals["input_cached"] = self.totals.get("input_cached", 0) + cache_read_tokens
        self.call_count += 1

    def reset(self) -> None:
        """Clear all accumulated counts (used by ``/new``)."""
        self.totals.clear()
        self.call_count = 0


__all__ = ["TokenUsage"]
