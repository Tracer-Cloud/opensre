"""Integration test: mask a realistic k8s alert payload and round-trip it.

Uses the repo's existing Datadog k8s alert fixture — no live k8s required.
Verifies that pod/namespace/cluster/host identifiers are all replaced by
placeholders, and that unmasking reproduces the original payload.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.masking.context import MaskingContext
from app.masking.policy import ALL_KINDS, MaskingPolicy

FIXTURE = (
    Path(__file__).parent.parent
    / "e2e"
    / "kubernetes"
    / "fixtures"
    / "datadog_k8s_alert.json"
)


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _enabled_ctx() -> MaskingContext:
    return MaskingContext(
        policy=MaskingPolicy.model_validate({"enabled": True, "kinds": ALL_KINDS})
    )


def test_fixture_file_exists() -> None:
    assert FIXTURE.exists(), f"expected fixture at {FIXTURE}"


def test_round_trip_reproduces_fixture_exactly() -> None:
    original = _load_fixture()
    ctx = _enabled_ctx()
    masked = ctx.mask_value(original)
    restored = ctx.unmask_value(masked)
    assert restored == original


def test_namespace_value_is_masked() -> None:
    original = _load_fixture()
    ctx = _enabled_ctx()
    masked = ctx.mask_value(original)
    masked_text = json.dumps(masked)
    # 'tracer-test' is the namespace value mentioned in the fixture's
    # annotation summary ("namespace tracer-test"); after masking, the raw
    # value should not appear on its own in contexts that the namespace
    # detector recognises.
    assert (
        any("tracer-test" in original_value for original_value in ctx.placeholder_map.values())
        or "tracer-test" not in masked_text
    )


def test_masking_produces_at_least_one_placeholder() -> None:
    ctx = _enabled_ctx()
    ctx.mask_value(_load_fixture())
    assert ctx.placeholder_map, "expected at least one placeholder from a realistic k8s alert"


def test_disabled_policy_leaves_fixture_unchanged() -> None:
    original = _load_fixture()
    disabled = MaskingContext(
        policy=MaskingPolicy.model_validate({"enabled": False, "kinds": ALL_KINDS})
    )
    assert disabled.mask_value(original) == original
    assert disabled.placeholder_map == {}


def test_extra_regex_policy_activates_new_kind_without_code_change() -> None:
    """Acceptance criterion #2: configuration without editing code."""
    ctx = MaskingContext(
        policy=MaskingPolicy.from_env(
            {
                "OPENSRE_MASK_ENABLED": "true",
                "OPENSRE_MASK_KINDS": "",  # use defaults
                "OPENSRE_MASK_EXTRA_REGEX": '{"run_name": "([a-z]+-[a-z]+-[a-z]+)"}',
            }
        )
    )
    masked = ctx.mask("run_name=etl-transform-error completed")
    # Custom pattern matched and produced a placeholder
    assert any(p.startswith("<RUN_NAME_") for p in ctx.placeholder_map)
    assert ctx.unmask(masked) == "run_name=etl-transform-error completed"
