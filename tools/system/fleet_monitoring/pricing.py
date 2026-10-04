"""Per-model token pricing for the dashboard's ``$/hr`` column.

``$/hr`` is a *projected hourly burn rate* derived from the trailing
60 s usage window, not the actual spend over the last hour. The
sampler now keeps input/output/cache buckets, so pricing applies the
right rate to each bucket instead of using the legacy 70/30 blend.

Rates come from litellm's bundled community-maintained price table
(~2.8k models) rather than a hand-vendored dict. We
read litellm's *local* snapshot directly from its packaged JSON file
instead of the shared ``litellm.model_cost`` global: that global is a
process-wide singleton populated from a live network fetch on
whichever import touches it first, so depending on it would make our
pricing nondeterministic based on unrelated import order elsewhere in
the process (confirmed in practice — a live fetch elsewhere had
already picked up a same-week model release our local snapshot didn't
have yet). This module is imported unconditionally by the always-on
dashboard sampler, so pricing lookups must stay a pure, deterministic
offline dict read regardless of what else the process has imported,
and must degrade to "no rates" rather than crash the sampler if the
data file is ever missing. A tiny local override table covers the
rare model litellm's snapshot hasn't picked up yet (a brand-new
release, or a routing alias OpenAI/Anthropic don't publish as their
own price-table row). Unknown models return ``None`` so the dashboard
renders ``-`` rather than inventing a rate.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, TypeGuard

# litellm imports tiktoken and resolves an encoding at module load time; under
# a frozen (PyInstaller) build that lookup fails unless this bootstrap runs
# first. This module imports from the bootstrap module unconditionally,
# so the encoding lookup is always resolved before litellm needs it.
from core.llm.transports.litellm.frozen_tiktoken_bootstrap import (
    ensure_tiktoken_encodings_discoverable,
)

ensure_tiktoken_encodings_discoverable()

from tools.system.fleet_monitoring.meters import TokenUsage  # noqa: E402

#: litellm's bundled price/context-window snapshot. Read directly (see
#: _litellm_local_cost_map) rather than via litellm.litellm_core_utils's
#: GetModelCostMap — that class is a private internal, not covered by
#: litellm's semver, and a rename there must not crash this always-on
#: sampler. The filename itself is already a load-bearing contract
#: elsewhere (tests/packaging/test_litellm_bundle_contract.py asserts it
#: ships in frozen release builds).
_LITELLM_LOCAL_PRICE_SNAPSHOT_FILENAME = "model_prices_and_context_window_backup.json"

#: Kept for callers that logged/displayed the vendored-table refresh date.
#: Rates are now sourced live (per-process) from litellm's own bundled table.
RATES_VERIFIED_AT = "litellm model_cost (local snapshot)"

_USD_PER_M = 1_000_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPrice:
    usd_per_input_token: float
    usd_per_output_token: float
    usd_per_cached_input_token: float | None = None
    usd_per_cache_read_input_token: float | None = None
    usd_per_cache_creation_input_token: float | None = None

    @property
    def cached_input_rate(self) -> float:
        return (
            self.usd_per_cached_input_token
            if self.usd_per_cached_input_token is not None
            else self.cache_read_rate
        )

    @property
    def cache_read_rate(self) -> float:
        return (
            self.usd_per_cache_read_input_token
            if self.usd_per_cache_read_input_token is not None
            else self.usd_per_input_token
        )

    @property
    def cache_creation_rate(self) -> float:
        return (
            self.usd_per_cache_creation_input_token
            if self.usd_per_cache_creation_input_token is not None
            else self.usd_per_input_token
        )


@dataclass(frozen=True)
class PriceOverride:
    """Per-agent rate override loaded from ``agents.yaml``.

    Overrides are USD per 1M input/output tokens. Cache rates keep the
    base model's ratios when the model is known; for custom unknown
    models they fall back to the effective input rate.
    """

    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None


def _price(
    input_usd_per_million: float,
    output_usd_per_million: float,
    *,
    cache_read_usd_per_million: float | None = None,
    cache_write_usd_per_million: float | None = None,
) -> ModelPrice:
    input_rate = input_usd_per_million / _USD_PER_M
    cache_read_rate = (
        cache_read_usd_per_million / _USD_PER_M if cache_read_usd_per_million is not None else None
    )
    return ModelPrice(
        usd_per_input_token=input_rate,
        usd_per_output_token=output_usd_per_million / _USD_PER_M,
        usd_per_cached_input_token=cache_read_rate,
        usd_per_cache_read_input_token=cache_read_rate,
        usd_per_cache_creation_input_token=(
            cache_write_usd_per_million / _USD_PER_M
            if cache_write_usd_per_million is not None
            else None
        ),
    )


# Models confirmed absent from litellm's bundled price table. This is an
# escape hatch for the rare model litellm's table hasn't (yet, or ever again)
# picked up, not a general config surface: entries here are only consulted
# after a direct litellm lookup misses.
_LOCAL_MODEL_PRICES: dict[str, ModelPrice] = {
    # GPT-5.6 (GA 2026-07-09). Per 1M tokens, from
    # https://developers.openai.com/api/docs/pricing: sol 5/30, terra
    # 2.50/15, luna 1/6. Cached input is 90% off. litellm's snapshot now
    # carries the family but with rates that diverge from OpenAI's
    # published table for terra/luna, so these rows stay authoritative
    # (see the family-fallback preference in _lookup_price).
    "gpt-5.6-sol": _price(5.00, 30.00, cache_read_usd_per_million=0.50),
    "gpt-5.6-terra": _price(2.50, 15.00, cache_read_usd_per_million=0.25),
    "gpt-5.6-luna": _price(1.00, 6.00, cache_read_usd_per_million=0.10),
    # claude-3-5-sonnet-20241022 — retired, frozen historical rate. litellm's
    # current table only keeps this generation under Bedrock-routed keys
    # (e.g. anthropic.claude-3-5-sonnet-20241022-v2:0), not the bare
    # direct-API id Claude Code CLI logs report.
    "claude-3-5-sonnet-20241022": _price(
        3.00, 15.00, cache_read_usd_per_million=0.30, cache_write_usd_per_million=3.75
    ),
    # Claude 5.x releases (September 2026) absent from litellm's bundled table.
    # Per 1M tokens, from Anthropic pricing:
    # Sonnet 5.5: 2.00 / 10.00, cache read 0.20, cache write 2.50.
    # Opus 5.5: 4.00 / 20.00, cache read 0.40, cache write 5.00.
    # Fable 5.1: 10.00 / 50.00, cache read 1.00, cache write 12.50.
    "claude-sonnet-5-5": _price(
        2.00, 10.00, cache_read_usd_per_million=0.20, cache_write_usd_per_million=2.50
    ),
    "claude-opus-5-5": _price(
        4.00, 20.00, cache_read_usd_per_million=0.40, cache_write_usd_per_million=5.00
    ),
    "claude-fable-5-1": _price(
        10.00, 50.00, cache_read_usd_per_million=1.00, cache_write_usd_per_million=12.50
    ),
}

# Longest-prefix-first so more specific tiers (e.g. ``gpt-5.6-terra``) win
# over the bare ``gpt-5.6`` alias. Built programmatically so a future edit
# cannot silently shadow a longer prefix with a shorter one.
_LOCAL_FAMILY_FALLBACKS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("gpt-5.6-sol", "gpt-5.6-sol"),
            ("gpt-5.6-terra", "gpt-5.6-terra"),
            ("gpt-5.6-luna", "gpt-5.6-luna"),
            # OpenAI routes the bare ``gpt-5.6`` alias to Sol server-side.
            ("gpt-5.6", "gpt-5.6-sol"),
            ("claude-sonnet-5-5", "claude-sonnet-5-5"),
            ("claude-opus-5-5", "claude-opus-5-5"),
            ("claude-fable-5-1", "claude-fable-5-1"),
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)

# OpenSRE routes these providers through a generic OpenAI-compatible HTTP
# client (core/llm/providers/openai_compat_providers.py) using the bare
# configured model name — never litellm's own "<provider>/<model>" prefix
# convention. litellm keys these two providers *under* that prefix
# regardless (e.g. "groq/llama-3.3-70b-versatile"), so a bare candidate
# would otherwise never match even though the model is genuinely priced.
# Tried only as a last resort (see _lookup_price), and scoped to providers
# OpenSRE actually configures — guessing a provider prefix for an arbitrary
# open-weight model would risk silently picking a different host's price.
_COMPAT_PROVIDER_PREFIXES: tuple[str, ...] = ("groq/", "minimax/")


def usd_for_usage(
    usage: TokenUsage,
    model: str | None,
    override: PriceOverride | None = None,
) -> float | None:
    """Return USD for a structured usage sample.

    Codex reports ``cached_input_tokens`` as a discounted subset of
    ``input_tokens``. If a future format reports cached input as a
    disjoint counter, clamp to the current convention and log at
    debug level instead of producing a negative non-cached input
    total.
    """
    price = _resolve_price(model, override)
    if price is None:
        return None

    input_tokens = max(0.0, usage.input_tokens)
    raw_cached_input_tokens = max(0.0, usage.cached_input_tokens)
    if raw_cached_input_tokens > input_tokens:
        logger.debug(
            "cached_input_tokens exceeded input_tokens; clamping to input total",
            extra={
                "model": model,
                "input_tokens": input_tokens,
                "cached_input_tokens": raw_cached_input_tokens,
            },
        )
    cached_input_tokens = min(raw_cached_input_tokens, input_tokens)
    non_cached_input_tokens = input_tokens - cached_input_tokens
    return (
        non_cached_input_tokens * price.usd_per_input_token
        + cached_input_tokens * price.cached_input_rate
        + max(0.0, usage.output_tokens) * price.usd_per_output_token
        + max(0.0, usage.cache_read_input_tokens) * price.cache_read_rate
        + max(0.0, usage.cache_creation_input_tokens) * price.cache_creation_rate
    )


def usd_per_hour_for_usage(
    usage_per_min: TokenUsage,
    model: str | None,
    override: PriceOverride | None = None,
) -> float | None:
    cost_per_min = usd_for_usage(usage_per_min, model, override)
    if cost_per_min is None:
        return None
    return cost_per_min * 60.0


def usd_per_token_blended(model: str | None, override: PriceOverride | None = None) -> float | None:
    price = _resolve_price(model, override)
    if price is None:
        return None
    return 0.7 * price.usd_per_input_token + 0.3 * price.usd_per_output_token


def usd_per_hour(
    tokens_per_min: float,
    model: str | None,
    override: PriceOverride | None = None,
) -> float | None:
    """Legacy blended API kept for callers/tests that only have a total."""
    rate = usd_per_token_blended(model, override)
    if rate is None:
        return None
    return tokens_per_min * 60.0 * rate


#: A candidate that still carries a hosting-surface routing artifact —
#: a provider path segment (``bedrock/...``), an Anthropic vendor-prefixed
#: Bedrock id (``us.anthropic....``), or a trailing Bedrock version suffix
#: (``-v1:0``). litellm indexes these as their own keys (same rate as the
#: bare id), so a raw candidate can resolve *before* the canonical
#: direct-API form is tried — this predicate lets ``normalize_model_name``
#: prefer the canonical form when both resolve.
_ROUTING_SUFFIX_RE = re.compile(r"-v\d+:\d+$")


def _is_canonical_candidate(candidate: str) -> bool:
    return (
        "/" not in candidate
        and "@" not in candidate
        and "anthropic." not in candidate
        and not _ROUTING_SUFFIX_RE.search(candidate)
    )


def _local_family_alias_canonical(candidate: str) -> str | None:
    """Map bare alias ids (prefix != canonical) over a litellm hit on the alias."""
    for prefix, canonical_id in _LOCAL_FAMILY_FALLBACKS:
        if candidate == prefix and prefix != canonical_id:
            return canonical_id
    return None


def _local_family_fallback_canonical(candidate: str) -> str | None:
    for prefix, canonical_id in _LOCAL_FAMILY_FALLBACKS:
        if candidate.startswith(prefix):
            return canonical_id
    return None


def normalize_model_name(model: str | None) -> str | None:
    if model is None:
        return None
    candidates = _model_candidates(model)
    resolving = [
        candidate
        for candidate in candidates
        if _litellm_price(candidate) is not None or candidate in _LOCAL_MODEL_PRICES
    ]
    if resolving:
        canonical_candidates = [
            candidate for candidate in resolving if _is_canonical_candidate(candidate)
        ]
        # Prefer the most specific canonical match (keeps a date suffix over
        # the bare family alias); fall back to any resolving candidate if
        # every match still carries a routing artifact.
        resolved = max(canonical_candidates or resolving, key=len)
        alias = _local_family_alias_canonical(resolved)
        return alias if alias is not None else resolved
    for candidate in candidates:
        fallback = _local_family_fallback_canonical(candidate)
        if fallback is not None:
            return fallback
    return candidates[0] if candidates else None


def _resolve_price(model: str | None, override: PriceOverride | None) -> ModelPrice | None:
    base = _lookup_price(model) if model is not None else None
    if override is None:
        return base

    input_rate = (
        override.input_usd_per_million / _USD_PER_M
        if override.input_usd_per_million is not None
        else (base.usd_per_input_token if base is not None else None)
    )
    output_rate = (
        override.output_usd_per_million / _USD_PER_M
        if override.output_usd_per_million is not None
        else (base.usd_per_output_token if base is not None else None)
    )
    if input_rate is None or output_rate is None:
        return None

    return ModelPrice(
        usd_per_input_token=input_rate,
        usd_per_output_token=output_rate,
        usd_per_cached_input_token=_override_related_rate(
            input_rate,
            base.usd_per_cached_input_token if base is not None else None,
            base.usd_per_input_token if base is not None else None,
        ),
        usd_per_cache_read_input_token=_override_related_rate(
            input_rate,
            base.usd_per_cache_read_input_token if base is not None else None,
            base.usd_per_input_token if base is not None else None,
        ),
        usd_per_cache_creation_input_token=_override_related_rate(
            input_rate,
            base.usd_per_cache_creation_input_token if base is not None else None,
            base.usd_per_input_token if base is not None else None,
        ),
    )


def _override_related_rate(
    effective_input_rate: float,
    base_related_rate: float | None,
    base_input_rate: float | None,
) -> float | None:
    if base_related_rate is None or base_input_rate is None or base_input_rate == 0.0:
        return None
    return effective_input_rate * (base_related_rate / base_input_rate)


def _lookup_price(model: str) -> ModelPrice | None:
    candidates = _model_candidates(model)
    for candidate in candidates:
        price = _litellm_price(candidate)
        if price is not None:
            # A family-fallback row pins the rate for every id in the family.
            # litellm may also carry some of those ids (it added gpt-5.6 with
            # divergent tier rates), so without this preference a bare tier id
            # and its suffixed siblings would price from different sources.
            canonical = _local_family_fallback_canonical(candidate)
            if canonical is not None:
                local = _LOCAL_MODEL_PRICES.get(canonical)
                if local is not None:
                    return local
            return price
        local = _LOCAL_MODEL_PRICES.get(candidate)
        if local is not None:
            return local
    for candidate in candidates:
        fallback = _local_family_fallback_canonical(candidate)
        if fallback is not None:
            return _LOCAL_MODEL_PRICES.get(fallback)
    for candidate in candidates:
        for provider_prefix in _COMPAT_PROVIDER_PREFIXES:
            price = _litellm_price(f"{provider_prefix}{candidate}")
            if price is not None:
                return price
    return None


@lru_cache(maxsize=1)
def _litellm_local_cost_map() -> dict[str, Any]:
    """litellm's bundled price snapshot, read directly (never the live fetch).

    Deliberately bypasses the shared ``litellm.model_cost`` global — see the
    module docstring for why that global isn't safe to depend on here. Reads
    the packaged JSON file directly rather than through litellm's internal
    ``GetModelCostMap`` class, and degrades to an empty table (every model
    reports unpriced) instead of raising if the file is ever missing or
    unreadable — this module is imported unconditionally by the always-on
    dashboard sampler, so a data-loading hiccup must never crash it.

    Keyed lowercase: our candidates are always lowercased (see
    ``_model_candidates``), but litellm's own keys aren't uniformly
    lowercase — e.g. MiniMax entries are ``minimax/MiniMax-M2.1``. A
    case-sensitive ``dict.get`` would silently miss those.
    """
    try:
        raw = json.loads(
            files("litellm")
            .joinpath(_LITELLM_LOCAL_PRICE_SNAPSHOT_FILENAME)
            .read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        logger.warning(
            "litellm local price snapshot unavailable; pricing lookups will report unpriced",
            exc_info=True,
        )
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key.lower(): value for key, value in raw.items()}


def _litellm_price(candidate: str) -> ModelPrice | None:
    """Look up a rate directly in litellm's local price snapshot.

    Reads rate fields rather than calling ``litellm.cost_per_token()`` so we
    keep per-bucket rates (not a computed amount) for the bucket math in
    :func:`usd_for_usage`. A miss returns ``None`` — never invents a rate.
    """
    entry = _litellm_local_cost_map().get(candidate)
    if not isinstance(entry, dict):
        return None
    input_rate = entry.get("input_cost_per_token")
    output_rate = entry.get("output_cost_per_token")
    if not _is_rate(input_rate) or not _is_rate(output_rate):
        return None
    return ModelPrice(
        usd_per_input_token=float(input_rate),
        usd_per_output_token=float(output_rate),
        usd_per_cache_read_input_token=_optional_rate(entry.get("cache_read_input_token_cost")),
        usd_per_cache_creation_input_token=_optional_rate(
            entry.get("cache_creation_input_token_cost")
        ),
    )


def _is_rate(value: object) -> TypeGuard[int | float]:
    # bool is rejected explicitly because isinstance(True, int) is True —
    # same guard as meters/__init__.py's safe_int, applied here so a stray
    # boolean in litellm's JSON can't be silently read as a 1 USD/token rate.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _optional_rate(value: object) -> float | None:
    return float(value) if _is_rate(value) else None


@lru_cache(maxsize=512)
def _model_candidates(raw: str) -> tuple[str, ...]:
    candidates: list[str] = []

    def append(value: str) -> None:
        normalized = value.strip().lower()
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    trimmed = raw.strip()
    append(trimmed)
    lower = trimmed.lower()
    # ``azure/`` (unlike gemini/deepseek/groq) is worth stripping explicitly:
    # Azure deployment names are arbitrary per customer, so falling back to
    # the bare model name is the only way a deployment literally named after
    # its underlying model (the common case) still resolves. The other
    # providers' litellm entries already carry both the prefixed and bare
    # form as distinct keys, so no stripping is needed for those.
    for prefix in ("openai/", "anthropic/", "anthropic.", "azure/"):
        if lower.startswith(prefix):
            append(trimmed[len(prefix) :])

    if "claude-" in lower and "." in trimmed:
        tail = trimmed.rsplit(".", maxsplit=1)[-1]
        if tail.lower().startswith("claude-"):
            append(tail)

    index = 0
    while index < len(candidates):
        candidate = candidates[index]
        if "@" in candidate:
            base, suffix = candidate.split("@", maxsplit=1)
            append(base)
            if re.fullmatch(r"\d{8}", suffix):
                append(f"{base}-{suffix}")
        elif candidate.startswith("claude-"):
            append(f"{candidate}@default")

        for pattern in (r"-\d{4}-\d{2}-\d{2}$", r"-\d{8}$", r"-v\d+:\d+$"):
            stripped = re.sub(pattern, "", candidate)
            if stripped != candidate:
                append(stripped)
        index += 1

    return tuple(candidates)


__all__ = [
    "ModelPrice",
    "PriceOverride",
    "RATES_VERIFIED_AT",
    "normalize_model_name",
    "usd_for_usage",
    "usd_per_hour",
    "usd_per_hour_for_usage",
    "usd_per_token_blended",
]
