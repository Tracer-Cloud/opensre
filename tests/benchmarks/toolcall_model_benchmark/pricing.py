"""LLM run cost estimate from token totals (no LangGraph / agent imports)."""

from __future__ import annotations

from typing import Any


def _classify_pricing_tier(model_id: str, reasoning_model: str, tool_model: str) -> str:
    mid = model_id.lower()
    if model_id == reasoning_model or mid == reasoning_model.lower():
        return "reasoning"
    if model_id == tool_model or mid == tool_model.lower():
        return "tool"
    if "haiku" in mid:
        return "tool"
    if "sonnet" in mid or "opus" in mid:
        return "reasoning"
    return "reasoning"


def _token_bucket_total(tt: Any) -> int:
    total = getattr(tt, "total", None)
    if callable(total):
        return int(total())
    if isinstance(total, int):
        return total
    inp = int(getattr(tt, "input_tokens", 0) or 0)
    out = int(getattr(tt, "output_tokens", 0) or 0)
    return inp + out


def estimate_run_cost_usd(
    tokens_by_model: dict[str, Any],
    *,
    reasoning_model: str,
    tool_model: str,
    reasoning_usd_per_mtok: float = 3.0,
    tool_usd_per_mtok: float = 1.0,
) -> tuple[float, dict[str, float]]:
    """Estimate USD: per model id, (input+output) tokens × $/MTok for that tier."""
    total_usd = 0.0
    breakdown_by_model: dict[str, float] = {}
    for model_id, tt in tokens_by_model.items():
        mtok = _token_bucket_total(tt) / 1_000_000.0
        tier = _classify_pricing_tier(model_id, reasoning_model, tool_model)
        rate = reasoning_usd_per_mtok if tier == "reasoning" else tool_usd_per_mtok
        usd = mtok * rate
        breakdown_by_model[model_id] = usd
        total_usd += usd
    return total_usd, breakdown_by_model
