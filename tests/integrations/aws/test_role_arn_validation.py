"""Role ARN shape is checked at the prompt; partial env credentials get their own message."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.aws.setup import validate_role_arn


@pytest.mark.parametrize(
    "value",
    [
        "arn:aws:iam::395261708130:role/OpenSREReadOnly",
        "  arn:aws:iam::123456789012:role/path/Nested  ",
    ],
)
def test_a_role_arn_is_accepted(value: str) -> None:
    assert validate_role_arn(value) is None


def test_the_roles_unique_id_is_rejected_with_a_pointer_to_the_arn() -> None:
    """The slip seen in the field: the ``AROA…`` unique ID pasted where the ARN belongs."""
    problem = validate_role_arn("AROAVYB3PA5RPMNFXNNJM")

    assert problem is not None
    assert "unique ID" in problem
    assert "arn:aws:iam::" in problem


def test_a_user_arn_is_rejected_and_told_a_role_is_needed() -> None:
    """The slip in the field: the user's own ARN pasted where a role belongs."""
    problem = validate_role_arn("arn:aws:iam::395261708130:user/yauhen")

    assert problem is not None
    assert "user" in problem and "role" in problem
    assert "Access Key + Secret" in problem
    assert "IAM → Roles" in problem


def test_a_bare_role_name_is_rejected_with_the_arn_pattern_filled_in() -> None:
    problem = validate_role_arn("OpenSREReadOnly")

    assert problem is not None
    assert "role/OpenSREReadOnly" in problem
    assert "<account-id>" in problem


def test_anything_else_is_rejected_with_an_example() -> None:
    problem = validate_role_arn("arn:aws:iam::123:role/")

    assert problem is not None
    assert "arn:aws:iam::123456789012:role/OpenSREReadOnly" in problem


def test_partial_env_credentials_name_the_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """AWS_ACCESS_KEY_ID set without its secret is not 'no credentials' — say which half is missing."""
    from botocore.exceptions import PartialCredentialsError

    from integrations.aws.verifier import ROLE_NEEDS_BASE_CREDENTIALS_MESSAGE, verify_aws

    class _BaseSTSClient:
        def assume_role(self, **_kwargs: Any) -> dict[str, Any]:
            raise PartialCredentialsError(provider="env", cred_var="AWS_SECRET_ACCESS_KEY")

    monkeypatch.setattr(
        "integrations.aws.verifier._base_sts_client", lambda _region: _BaseSTSClient()
    )

    result = verify_aws(
        "setup", {"role_arn": "arn:aws:iam::123456789012:role/x", "region": "us-east-1"}
    )

    assert result["status"] == "failed"
    # Half a key pair is its own situation — not "none were found" plus a footnote.
    assert ROLE_NEEDS_BASE_CREDENTIALS_MESSAGE not in result["detail"]
    assert "AWS_SECRET_ACCESS_KEY is not set" in result["detail"]
    assert "set both" in result["detail"]
