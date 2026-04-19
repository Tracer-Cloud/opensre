"""Tests for MaskingPolicy — validation and env-var loading."""

from __future__ import annotations

import pytest

from app.masking.policy import ALL_KINDS, MaskingPolicy


def test_default_policy_is_disabled() -> None:
    policy = MaskingPolicy()
    assert policy.enabled is False
    assert policy.kinds == ALL_KINDS
    assert policy.extra_patterns == {}


def test_from_env_enabled_true() -> None:
    policy = MaskingPolicy.from_env({"OPENSRE_MASK_ENABLED": "true"})
    assert policy.enabled is True
    assert policy.kinds == ALL_KINDS


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("", False),
        ("garbage", False),
    ],
)
def test_from_env_bool_parsing(raw: str, expected: bool) -> None:
    policy = MaskingPolicy.from_env({"OPENSRE_MASK_ENABLED": raw})
    assert policy.enabled is expected


def test_from_env_kinds_subset() -> None:
    policy = MaskingPolicy.from_env(
        {"OPENSRE_MASK_ENABLED": "true", "OPENSRE_MASK_KINDS": "pod,namespace"}
    )
    assert policy.kinds == ("pod", "namespace")


def test_from_env_kinds_unknown_kind_is_ignored() -> None:
    policy = MaskingPolicy.from_env(
        {"OPENSRE_MASK_ENABLED": "true", "OPENSRE_MASK_KINDS": "pod,not_a_real_kind,email"}
    )
    assert policy.kinds == ("pod", "email")


def test_from_env_kinds_all_invalid_falls_back_to_defaults() -> None:
    policy = MaskingPolicy.from_env(
        {"OPENSRE_MASK_ENABLED": "true", "OPENSRE_MASK_KINDS": "nope,also_nope"}
    )
    assert policy.kinds == ALL_KINDS


def test_from_env_extra_regex_parsed() -> None:
    policy = MaskingPolicy.from_env(
        {
            "OPENSRE_MASK_ENABLED": "true",
            "OPENSRE_MASK_EXTRA_REGEX": '{"jira_key": "\\\\b[A-Z]+-\\\\d+\\\\b"}',
        }
    )
    assert "jira_key" in policy.extra_patterns


def test_from_env_invalid_json_extra_regex_ignored() -> None:
    policy = MaskingPolicy.from_env(
        {"OPENSRE_MASK_ENABLED": "true", "OPENSRE_MASK_EXTRA_REGEX": "not valid json"}
    )
    assert policy.extra_patterns == {}


def test_invalid_regex_in_extra_patterns_raises() -> None:
    with pytest.raises(ValueError, match="not a valid regex"):
        MaskingPolicy.model_validate(
            {"enabled": True, "kinds": ALL_KINDS, "extra_patterns": {"bad": "["}}
        )


def test_is_kind_enabled_respects_enabled_flag() -> None:
    policy = MaskingPolicy.model_validate({"enabled": False, "kinds": ("pod",)})
    assert policy.is_kind_enabled("pod") is False


def test_is_kind_enabled_filters_by_selected_kinds() -> None:
    policy = MaskingPolicy.model_validate({"enabled": True, "kinds": ("pod", "namespace")})
    assert policy.is_kind_enabled("pod") is True
    assert policy.is_kind_enabled("email") is False
