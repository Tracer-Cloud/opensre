"""Tests for MaskingContext — mask/unmask round-trip, stability, structured payloads."""

from __future__ import annotations

from app.masking.context import MaskingContext
from app.masking.policy import ALL_KINDS, MaskingPolicy


def _enabled_ctx() -> MaskingContext:
    policy = MaskingPolicy.model_validate({"enabled": True, "kinds": ALL_KINDS})
    return MaskingContext(policy=policy)


def _disabled_ctx() -> MaskingContext:
    policy = MaskingPolicy.model_validate({"enabled": False, "kinds": ALL_KINDS})
    return MaskingContext(policy=policy)


def test_disabled_policy_is_identity() -> None:
    ctx = _disabled_ctx()
    text = "pod etl-worker-7d9f8b-xkp2q failing in kube_namespace:tracer-test"
    assert ctx.mask(text) == text
    assert ctx.placeholder_map == {}


def test_repeated_identifier_maps_to_same_placeholder() -> None:
    ctx = _enabled_ctx()
    text_a = ctx.mask("kube_namespace:payments-prod saw error")
    text_b = ctx.mask("second alert also in kube_namespace:payments-prod")
    # Same real value should reuse the same placeholder.
    ns_placeholder = next(
        ph for ph, original in ctx.placeholder_map.items() if original == "payments-prod"
    )
    assert ns_placeholder in text_a
    assert ns_placeholder in text_b
    assert sum(1 for _ in ctx.placeholder_map.values() if _ == "payments-prod") == 1


def test_round_trip_restores_originals() -> None:
    ctx = _enabled_ctx()
    original = (
        "pod etl-worker-7d9f8b-xkp2q in kube_namespace:tracer-test from "
        "host kind-control-plane account 123456789012 email alice@example.com"
    )
    masked = ctx.mask(original)
    assert masked != original
    assert ctx.unmask(masked) == original


def test_structured_payload_recursive_mask_and_unmask() -> None:
    ctx = _enabled_ctx()
    payload = {
        "alert_name": "PodCrashLoop",
        "tags": ["kube_namespace:prod", "pod:worker-7d9f8b-abcde"],
        "nested": {
            "host": "kind-control-plane",
            "ip": "192.168.1.50",
        },
    }
    masked = ctx.mask_value(payload)
    assert masked["alert_name"] == "PodCrashLoop"
    # kube_namespace now contains a placeholder, not "prod"
    assert all("prod" not in tag for tag in masked["tags"] if "kube_namespace" in tag)
    assert "kind-control-plane" not in str(masked["nested"])
    assert "192.168.1.50" not in str(masked["nested"])

    restored = ctx.unmask_value(masked)
    assert restored == payload


def test_placeholder_format_is_stable() -> None:
    ctx = _enabled_ctx()
    ctx.mask("kube_namespace:alpha")
    ctx.mask("kube_namespace:bravo")
    placeholders = list(ctx.placeholder_map.keys())
    assert any(p.startswith("<NAMESPACE_") for p in placeholders)
    # Unique placeholders for distinct originals
    assert len(placeholders) == len(set(placeholders))


def test_placeholder_collisions_are_prevented() -> None:
    ctx = _enabled_ctx()
    ctx.mask("host kind-control-plane")
    ctx.mask("kube_namespace:kind-control-plane")  # different kind, same value
    # Even though the same string "kind-control-plane" appears in two contexts,
    # the reverse_map prevents allocating a new placeholder.
    values = list(ctx.placeholder_map.values())
    assert values.count("kind-control-plane") == 1


def test_from_state_reconstructs_context() -> None:
    ctx = _enabled_ctx()
    masked = ctx.mask("kube_namespace:prod incident")
    # Simulate state carrying the map between nodes
    state = {"masking_map": ctx.to_state()}
    reconstructed = MaskingContext.from_state(state)
    assert reconstructed.unmask(masked) == "kube_namespace:prod incident"


def test_unmask_empty_map_is_identity() -> None:
    ctx = _enabled_ctx()
    assert ctx.unmask("nothing here") == "nothing here"


def test_non_string_values_pass_through() -> None:
    ctx = _enabled_ctx()
    assert ctx.mask_value(42) == 42
    assert ctx.mask_value(None) is None
    assert ctx.mask_value(True) is True


def test_empty_string_masked_to_empty_string() -> None:
    ctx = _enabled_ctx()
    assert ctx.mask("") == ""
    assert ctx.unmask("") == ""


def test_counters_stable_when_map_iterated_out_of_order() -> None:
    """Regression: a placeholder map with <NS_2> before <NS_0> must not
    inflate the counter (previously yielded 4 instead of 3)."""
    policy = MaskingPolicy.model_validate({"enabled": True, "kinds": ALL_KINDS})
    # Intentionally insert in non-ascending index order
    seeded = {
        "<NAMESPACE_2>": "gamma",
        "<NAMESPACE_0>": "alpha",
        "<NAMESPACE_1>": "beta",
    }
    ctx = MaskingContext(policy=policy, placeholder_map=seeded)
    # Allocate a fresh namespace; its index must be exactly 3 (one past the max).
    masked = ctx.mask("kube_namespace:delta fresh")
    fresh_placeholder = next(
        p for p, v in ctx.placeholder_map.items() if v == "delta"
    )
    assert fresh_placeholder == "<NAMESPACE_3>"
    assert "<NAMESPACE_3>" in masked


def test_partial_overlap_matches_do_not_corrupt_text() -> None:
    """Regression: if a custom extra regex overlaps partially with a built-in
    detector, both matches must not survive or _apply_replacements splices
    overlapping spans and produces corrupted output."""
    # Custom pattern that partially overlaps with the IP detector region.
    policy = MaskingPolicy.model_validate(
        {
            "enabled": True,
            "kinds": ("ip_address",),
            # Matches "192.168.1." inside "192.168.1.50" — partial overlap with
            # the IP detector's full 192.168.1.50 span.
            "extra_patterns": {"partial": r"(192\.168\.1\.)"},
        }
    )
    ctx = MaskingContext(policy=policy)
    masked = ctx.mask("host 192.168.1.50 online")
    # Exactly one placeholder used; the other overlap was dropped.
    assert masked.count("<") == 1
    # Round trip returns the original text byte-for-byte.
    assert ctx.unmask(masked) == "host 192.168.1.50 online"
