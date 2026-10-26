"""Unmask must be one scan, not one full-text replace per placeholder.

Characterization pins observable behavior: prefix-safe placeholders,
round-trip, empty/missing map, and no re-expansion of placeholder-shaped
text that appears inside an original value.
"""

from __future__ import annotations

from infrastructure.safety.masking.policy import ALL_KINDS, MaskingPolicy
from infrastructure.safety.masking.rules import MaskingRules


def _ctx(placeholder_map: dict[str, str] | None = None) -> MaskingRules:
    policy = MaskingPolicy.model_validate({"enabled": True, "kinds": ALL_KINDS})
    return MaskingRules(policy=policy, placeholder_map=placeholder_map)


def test_unmask_prefers_full_token_over_numeric_prefix() -> None:
    """``<NS_10>`` must not be partially rewritten by ``<NS_1>``."""
    ctx = _ctx(
        {
            "<NAMESPACE_1>": "alpha",
            "<NAMESPACE_10>": "omega",
        }
    )
    assert ctx.unmask("before <NAMESPACE_10> after") == "before omega after"
    assert ctx.unmask("<NAMESPACE_1> and <NAMESPACE_10>") == "alpha and omega"


def test_unmask_round_trip_many_placeholders() -> None:
    ctx = _ctx()
    original = (
        "kube_namespace:payments-prod pod worker-7d9f8b-abcde "
        "host kind-control-plane account 123456789012"
    )
    masked = ctx.mask(original)
    assert "<" in masked
    assert ctx.unmask(masked) == original


def test_unmask_leaves_unknown_angle_tokens() -> None:
    ctx = _ctx({"<NAMESPACE_0>": "prod"})
    text = "keep <NOT_A_REAL_PLACEHOLDER> and restore <NAMESPACE_0>"
    assert ctx.unmask(text) == "keep <NOT_A_REAL_PLACEHOLDER> and restore prod"


def test_unmask_does_not_rescan_replacement_text() -> None:
    """Originals may contain angle-bracket text; it is not a second unmask pass."""
    ctx = _ctx(
        {
            "<NAMESPACE_0>": "value-with-<HOST_0>-inside",
            "<HOST_0>": "should-not-appear",
        }
    )
    assert ctx.unmask("x <NAMESPACE_0> y") == "x value-with-<HOST_0>-inside y"


def test_unmask_empty_and_no_placeholders_are_identity() -> None:
    ctx = _ctx({"<NAMESPACE_0>": "prod"})
    assert ctx.unmask("") == ""
    assert ctx.unmask("no placeholders here") == "no placeholders here"
    empty = _ctx()
    assert empty.unmask("still <NAMESPACE_0> literal") == "still <NAMESPACE_0> literal"
