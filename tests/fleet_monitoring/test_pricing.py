"""Tests for the model pricing table and $/hr computation."""

from __future__ import annotations

import logging

import pytest

from tools.system.fleet_monitoring.meters import TokenUsage
from tools.system.fleet_monitoring.pricing import (
    PriceOverride,
    normalize_model_name,
    usd_for_usage,
    usd_per_hour,
    usd_per_hour_for_usage,
    usd_per_token_blended,
)


class TestUsdPerTokenBlended:
    def test_known_claude_model(self) -> None:
        # claude-sonnet-4-5 (litellm's model_cost table): 3 USD/M input,
        # 15 USD/M output, 70/30 blend.
        # Expected: 0.7 * 3e-6 + 0.3 * 15e-6 = 2.1e-6 + 4.5e-6 = 6.6e-6.
        rate = usd_per_token_blended("claude-sonnet-4-5")
        assert rate is not None
        assert rate == pytest.approx(6.6e-6, rel=1e-9)

    def test_known_gpt5_model(self) -> None:
        # gpt-5: 1.25 USD/M input, 10 USD/M output, 70/30 blend.
        # Expected: 0.7 * 1.25e-6 + 0.3 * 10e-6 = 0.875e-6 + 3e-6 = 3.875e-6.
        rate = usd_per_token_blended("gpt-5")
        assert rate is not None
        assert rate == pytest.approx(3.875e-6, rel=1e-9)

    def test_unknown_model_returns_none(self) -> None:
        # The dashboard renders ``-`` for unknown models rather than
        # inventing a price. The contract is "never invent".
        assert usd_per_token_blended("claude-galaxy-9000") is None

    def test_none_model_returns_none(self) -> None:
        # Meters emit ``None`` when no chunk carries a model hint;
        # the pricing module must accept that without raising.
        assert usd_per_token_blended(None) is None

    def test_dated_suffix_falls_back_to_family(self) -> None:
        # ``claude-sonnet-4-5-20251015`` (a future release id litellm's
        # bundled table does not have yet) resolves via the bare family
        # alias, which litellm does carry.
        rate = usd_per_token_blended("claude-sonnet-4-5-20251015")
        assert rate == usd_per_token_blended("claude-sonnet-4-5")

    def test_dated_sibling_does_not_collide_with_shorter_family(self) -> None:
        # ``claude-opus-4-1`` must resolve to its own rate rather than
        # collapsing onto an unrelated, older sibling in the same family.
        opus_4_20250514 = usd_per_token_blended("claude-opus-4-20250514")
        opus_4_1 = usd_per_token_blended("claude-opus-4-1")
        assert opus_4_20250514 is not None
        assert opus_4_1 is not None

    def test_price_matches_litellm_local_snapshot_directly(self) -> None:
        # Regression guard that this is actually wired to litellm's *local*
        # snapshot — not a hardcoded number that happens to agree with it
        # today, and not the shared ``litellm.model_cost`` global (which can
        # diverge from the local snapshot after a live fetch elsewhere in
        # the process; see the module docstring).
        from tools.system.fleet_monitoring.pricing import _litellm_local_cost_map

        entry = _litellm_local_cost_map()["claude-sonnet-4-5"]
        expected = 0.7 * entry["input_cost_per_token"] + 0.3 * entry["output_cost_per_token"]
        assert usd_per_token_blended("claude-sonnet-4-5") == pytest.approx(expected)

    def test_local_snapshot_loads_a_substantial_table(self) -> None:
        # Startup-time smoke guard: if litellm's packaged JSON file is ever
        # missing/unreadable, _litellm_local_cost_map degrades to an empty
        # dict rather than crashing the always-on sampler (see its
        # docstring) — but that failure mode must be loud in CI, not silent.
        from tools.system.fleet_monitoring.pricing import _litellm_local_cost_map

        assert len(_litellm_local_cost_map()) > 1000


class TestGpt56Family:
    """GPT-5.6 Sol / Terra / Luna — local overrides for published OpenAI rates.

    litellm ships rows for this family, but terra/luna diverge from
    https://developers.openai.com/api/docs/pricing (per 1M tokens:
    sol 5/30, terra 2.50/15, luna 1/6). Cached input is 90% off.
    """

    @pytest.mark.parametrize(
        ("model", "input_usd_per_million", "output_usd_per_million"),
        [
            ("gpt-5.6-sol", 5.00, 30.00),
            ("gpt-5.6-terra", 2.50, 15.00),
            ("gpt-5.6-luna", 1.00, 6.00),
        ],
    )
    def test_published_rates(
        self, model: str, input_usd_per_million: float, output_usd_per_million: float
    ) -> None:
        from tools.system.fleet_monitoring.pricing import _LOCAL_MODEL_PRICES

        price = _LOCAL_MODEL_PRICES[model]
        assert price.usd_per_input_token == pytest.approx(input_usd_per_million / 1e6)
        assert price.usd_per_output_token == pytest.approx(output_usd_per_million / 1e6)
        # Cached input reads are 90% off uncached input.
        assert price.usd_per_cache_read_input_token == pytest.approx(
            input_usd_per_million / 1e6 * 0.10
        )

    def test_sol_is_not_priced_as_base_gpt5(self) -> None:
        # The regression this whole entry exists to prevent: before #3931,
        # ``gpt-5.6-sol`` matched the ``gpt-5`` family catch-all and was
        # silently billed at the base gpt-5 rate (1.25/10) — a 4x
        # under-report on the flagship, with no error and no ``-`` cell.
        assert usd_per_token_blended("gpt-5.6-sol") != usd_per_token_blended("gpt-5")

    def test_tiers_are_distinctly_priced(self) -> None:
        # Each tier must resolve to its own rate rather than collapsing
        # onto a sibling via the shared ``gpt-5.6`` prefix.
        sol = usd_per_token_blended("gpt-5.6-sol")
        terra = usd_per_token_blended("gpt-5.6-terra")
        luna = usd_per_token_blended("gpt-5.6-luna")
        assert sol is not None and terra is not None and luna is not None
        assert sol > terra > luna

    def test_bare_alias_resolves_to_sol(self) -> None:
        # OpenAI routes the bare ``gpt-5.6`` alias to Sol server-side, so
        # billing it as anything else would mis-report real spend.
        assert usd_per_token_blended("gpt-5.6") == usd_per_token_blended("gpt-5.6-sol")
        assert normalize_model_name("gpt-5.6") == "gpt-5.6-sol"

    def test_unknown_tier_suffix_does_not_borrow_sol_rate(self) -> None:
        # ``gpt-5.6-terra-preview`` must land on terra, not on Sol via the
        # shorter ``gpt-5.6`` alias prefix. This is what the per-tier
        # family rows buy us over a single alias row.
        assert usd_per_token_blended("gpt-5.6-terra-preview") == usd_per_token_blended(
            "gpt-5.6-terra"
        )

    def test_openai_provider_prefix_resolves(self) -> None:
        # OpenRouter-style ids (``openai/gpt-5.6-sol``) are stripped to the
        # bare model before lookup.
        assert usd_per_token_blended("openai/gpt-5.6-sol") == usd_per_token_blended("gpt-5.6-sol")


class TestUsdPerHour:
    def test_zero_tokens_per_min_is_zero_cost(self) -> None:
        # An idle agent costs $0/hr — the cell shows ``$0.00``, not
        # ``-`` (because the model is known).
        assert usd_per_hour(0.0, "claude-sonnet-4-5") == pytest.approx(0.0)

    def test_unknown_model_returns_none(self) -> None:
        # Even with real tokens flowing, unknown model means ``-``.
        assert usd_per_hour(1000.0, "claude-galaxy-9000") is None

    def test_none_model_returns_none(self) -> None:
        assert usd_per_hour(1000.0, None) is None

    def test_formula_matches_tokens_per_min_times_60_times_rate(self) -> None:
        # The contract is ``tokens_per_min × 60 × rate``. Locking
        # this in so a refactor that switches to ``per second`` or
        # ``per hour`` directly does not change the dashboard's units.
        rate = usd_per_token_blended("claude-sonnet-4-5")
        assert rate is not None
        assert usd_per_hour(500.0, "claude-sonnet-4-5") == pytest.approx(500.0 * 60.0 * rate)

    def test_realistic_sonnet_session_under_a_dollar_per_hour(self) -> None:
        # Sanity check the numbers come out at a believable scale.
        # 200 tokens/min on Sonnet-4.5 (typical light agentic
        # session) should land under $0.10/hr.
        cost = usd_per_hour(200.0, "claude-sonnet-4-5")
        assert cost is not None
        assert 0.0 < cost < 0.10


class TestUsdForUsage:
    def test_codex_cached_input_uses_discounted_rate(self) -> None:
        usage = TokenUsage(input_tokens=1000, cached_input_tokens=250, output_tokens=100)
        cost = usd_for_usage(usage, "gpt-5-codex")
        expected = (750 * 1.25e-6) + (250 * 0.125e-6) + (100 * 10e-6)
        assert cost == pytest.approx(expected)

    def test_codex_cached_input_is_clamped_to_input(self) -> None:
        usage = TokenUsage(input_tokens=100, cached_input_tokens=500, output_tokens=0)
        cost = usd_for_usage(usage, "gpt-5-codex")
        assert cost == pytest.approx(100 * 0.125e-6)

    def test_codex_cached_input_clamp_logs_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        usage = TokenUsage(input_tokens=100, cached_input_tokens=500)
        with caplog.at_level(logging.DEBUG, logger="tools.system.fleet_monitoring.pricing"):
            usd_for_usage(usage, "gpt-5-codex")

        assert "cached_input_tokens exceeded input_tokens" in caplog.text

    def test_claude_cache_buckets_use_separate_rates(self) -> None:
        usage = TokenUsage(
            input_tokens=100,
            cache_read_input_tokens=2000,
            cache_creation_input_tokens=500,
            output_tokens=50,
        )
        cost = usd_for_usage(usage, "claude-sonnet-4-5")
        expected = (100 * 3e-6) + (2000 * 0.3e-6) + (500 * 3.75e-6) + (50 * 15e-6)
        assert cost == pytest.approx(expected)

    def test_hourly_usage_rate_projects_cost_per_minute(self) -> None:
        usage = TokenUsage(input_tokens=1000, cached_input_tokens=250, output_tokens=100)
        per_min_cost = usd_for_usage(usage, "gpt-5-codex")
        assert per_min_cost is not None
        assert usd_per_hour_for_usage(usage, "gpt-5-codex") == pytest.approx(per_min_cost * 60.0)

    def test_unknown_model_returns_none(self) -> None:
        assert usd_for_usage(TokenUsage(input_tokens=100), "claude-galaxy-9000") is None

    def test_input_output_overrides_keep_cache_ratio_for_known_model(self) -> None:
        usage = TokenUsage(input_tokens=1000, cached_input_tokens=100, output_tokens=10)
        override = PriceOverride(input_usd_per_million=2.0, output_usd_per_million=20.0)
        cost = usd_for_usage(usage, "gpt-5-codex", override)
        expected = (900 * 2e-6) + (100 * 0.2e-6) + (10 * 20e-6)
        assert cost == pytest.approx(expected)


class TestNormalizeModelName:
    def test_openai_prefix_is_stripped(self) -> None:
        assert normalize_model_name("openai/gpt-5.3-codex") == "gpt-5.3-codex"

    def test_anthropic_prefix_is_stripped(self) -> None:
        assert normalize_model_name("anthropic/claude-sonnet-4-5") == "claude-sonnet-4-5"

    def test_bedrock_style_claude_id_resolves_to_base(self) -> None:
        assert (
            normalize_model_name("us.anthropic.claude-sonnet-4-5-20250929-v1:0")
            == "claude-sonnet-4-5-20250929"
        )

    def test_at_default_variant_resolves_to_base(self) -> None:
        assert normalize_model_name("claude-sonnet-4-5@default") == "claude-sonnet-4-5"

    def test_legacy_direct_api_id_normalizes_to_itself(self) -> None:
        # claude-3-5-sonnet-20241022 is the confirmed litellm gap (only
        # present under Bedrock-routed keys upstream) — covered by the
        # local override table, and must still normalize cleanly.
        assert normalize_model_name("claude-3-5-sonnet-20241022") == "claude-3-5-sonnet-20241022"


class TestFamilyFallbackCoherence:
    """Drift guards on ``_LOCAL_FAMILY_FALLBACKS`` ↔ ``_LOCAL_MODEL_PRICES``."""

    def test_family_fallbacks_are_longest_prefix_first(self) -> None:
        from tools.system.fleet_monitoring.pricing import _LOCAL_FAMILY_FALLBACKS

        lengths = [len(prefix) for prefix, _canonical_id in _LOCAL_FAMILY_FALLBACKS]
        assert lengths == sorted(lengths, reverse=True)

    def test_every_family_fallback_canonical_id_has_a_price(self) -> None:
        # Without this guard, a typo in ``_LOCAL_FAMILY_FALLBACKS``'s
        # canonical id would silently break the family-prefix path:
        # ``_lookup_price`` would return ``None`` for what looks like
        # a known model and the dashboard would render ``-``.
        from tools.system.fleet_monitoring.pricing import (
            _LOCAL_FAMILY_FALLBACKS,
            _LOCAL_MODEL_PRICES,
        )

        for prefix, canonical_id in _LOCAL_FAMILY_FALLBACKS:
            assert canonical_id in _LOCAL_MODEL_PRICES, (
                f"family prefix {prefix!r} → canonical {canonical_id!r} not present "
                "in _LOCAL_MODEL_PRICES"
            )


class TestKnownModelCoverage:
    def test_claude_code_default_models_have_prices(self) -> None:
        # Defensive regression: the most common claude-code models
        # must each return a price so the dashboard does not silently
        # degrade to ``-`` after a routine model bump.
        # claude-3-5-sonnet-20241022 is litellm's confirmed legacy gap,
        # covered by the local override table.
        for model in ("claude-sonnet-4-5", "claude-opus-4-1", "claude-3-5-sonnet-20241022"):
            assert usd_per_token_blended(model) is not None

    def test_claude_fable_5_has_a_price(self) -> None:
        # #3621: claude-fable-5 must not render ``-`` on the dashboard.
        # $10/$50 per MTok, cache read $1.00, cache write $12.50.
        usage = TokenUsage(
            input_tokens=100,
            cache_read_input_tokens=2000,
            cache_creation_input_tokens=500,
            output_tokens=50,
        )
        cost = usd_for_usage(usage, "claude-fable-5")
        expected = (100 * 10e-6) + (2000 * 1.0e-6) + (500 * 12.5e-6) + (50 * 50e-6)
        assert cost == pytest.approx(expected)

    def test_claude_fable_5_dated_suffix_falls_back_to_family(self) -> None:
        # A future date-suffixed release id resolves via the family prefix.
        rate = usd_per_token_blended("claude-fable-5-20260609")
        assert rate is not None
        assert rate == usd_per_token_blended("claude-fable-5")

    def test_codex_default_models_have_prices(self) -> None:
        # Same guarantee for the codex side. ``gpt-5-codex`` is the
        # default model the Codex CLI configures for paid accounts.
        for model in ("gpt-5", "gpt-5-codex", "gpt-4o"):
            assert usd_per_token_blended(model) is not None

    @pytest.mark.parametrize(
        ("model_id", "input_per_million", "output_per_million", "cache_read", "cache_write"),
        (
            ("claude-sonnet-5-5", 2.00, 10.00, 0.20, 2.50),
            ("claude-opus-5-5", 4.00, 20.00, 0.40, 5.00),
            ("claude-fable-5-1", 10.00, 50.00, 1.00, 12.50),
        ),
    )
    def test_new_claude_models_have_prices(
        self,
        model_id: str,
        input_per_million: float,
        output_per_million: float,
        cache_read: float,
        cache_write: float,
    ) -> None:
        usage = TokenUsage(
            input_tokens=100,
            cache_read_input_tokens=2000,
            cache_creation_input_tokens=500,
            output_tokens=50,
        )
        cost = usd_for_usage(usage, model_id)
        expected = (
            (100 * input_per_million)
            + (2000 * cache_read)
            + (500 * cache_write)
            + (50 * output_per_million)
        ) * 1e-6
        assert cost == pytest.approx(expected)


class TestConfiguredProviderCompatibility:
    """Coverage sweep across every provider in config/llm_auth/provider_catalog.py.

    Providers wired through ``core/llm/providers/openai_compat_providers.py``
    (openrouter, deepseek, gemini, nvidia, minimax, groq, ollama) report a
    *bare* model name — never litellm's own ``<provider>/<model>`` prefix
    convention (see ``select_compat_model``). These tests use that real wire
    format, not a guessed one.
    """

    def test_deepseek_bare_model_has_a_price(self) -> None:
        assert usd_per_token_blended("deepseek-chat") is not None

    def test_gemini_bare_model_has_a_price(self) -> None:
        assert usd_per_token_blended("gemini-2.5-pro") is not None

    def test_azure_deployment_named_after_its_model_has_a_price(self) -> None:
        # Azure deployment names are arbitrary; this is the common case
        # where the deployment happens to be named after its model.
        assert usd_per_token_blended("gpt-4o") is not None

    def test_groq_bare_model_resolves_via_compat_provider_fallback(self) -> None:
        # litellm only keys this under "groq/llama-3.3-70b-versatile"; the
        # bare id Groq's own API (and OpenSRE's wire format) actually uses
        # must still resolve.
        assert usd_per_token_blended("llama-3.3-70b-versatile") is not None

    def test_minimax_bare_mixed_case_model_resolves(self) -> None:
        # litellm keys MiniMax under "minimax/MiniMax-M2.1" (mixed case);
        # OpenSRE's wire format sends the bare, differently-cased id.
        assert usd_per_token_blended("MiniMax-M2.1") is not None

    def test_nvidia_nim_is_unpriced_not_invented(self) -> None:
        # litellm's snapshot has zero NVIDIA NIM coverage today. This must
        # render "-" on the dashboard (via PriceOverride/agents.yaml), never
        # a guessed rate. If this starts failing because litellm added NIM
        # coverage, that's a welcome change — update this test, don't just
        # delete it.
        assert usd_per_token_blended("meta/llama-3.1-70b-instruct") is None

    def test_ollama_self_hosted_is_unpriced(self) -> None:
        # Self-hosted models have no per-token API price to look up; this is
        # exactly the gap the compute-basis ($/GPU-hr) pricing_basis from
        # the #3965 discussion is for, not something litellm can answer.
        assert usd_per_token_blended("llama3") is None
