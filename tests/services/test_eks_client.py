"""Tests for EKSClient credential resolution (stored creds vs AssumeRole)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.eks.eks_client import EKSClient
from app.services.eks.utils import stored_credentials_to_aws_creds

# ---------------------------------------------------------------------------
# stored_credentials_to_aws_creds
# ---------------------------------------------------------------------------


def test_stored_creds_helper_full_triplet() -> None:
    result = stored_credentials_to_aws_creds(
        {
            "access_key_id": "AKIA_TEST",
            "secret_access_key": "SECRET",
            "session_token": "TOKEN",
        }
    )
    assert result == {
        "AccessKeyId": "AKIA_TEST",
        "SecretAccessKey": "SECRET",
        "SessionToken": "TOKEN",
    }


def test_stored_creds_helper_empty_session_token_coerced_to_none() -> None:
    result = stored_credentials_to_aws_creds(
        {
            "access_key_id": "AKIA_TEST",
            "secret_access_key": "SECRET",
            "session_token": "",
        }
    )
    assert result is not None
    assert result["SessionToken"] is None


def test_stored_creds_helper_missing_session_token() -> None:
    result = stored_credentials_to_aws_creds(
        {
            "access_key_id": "AKIA_TEST",
            "secret_access_key": "SECRET",
        }
    )
    assert result is not None
    assert result["SessionToken"] is None


def test_stored_creds_helper_missing_access_key_returns_none() -> None:
    assert (
        stored_credentials_to_aws_creds({"secret_access_key": "SECRET", "session_token": "TOKEN"})
        is None
    )


def test_stored_creds_helper_missing_secret_key_returns_none() -> None:
    assert (
        stored_credentials_to_aws_creds({"access_key_id": "AKIA_TEST", "session_token": "TOKEN"})
        is None
    )


def test_stored_creds_helper_none_input() -> None:
    assert stored_credentials_to_aws_creds(None) is None


def test_stored_creds_helper_empty_dict() -> None:
    assert stored_credentials_to_aws_creds({}) is None


# ---------------------------------------------------------------------------
# EKSClient credential-resolution priority
# ---------------------------------------------------------------------------


def test_eks_client_stored_credentials_skip_assume_role() -> None:
    """With valid stored credentials, STS AssumeRole must not be called."""
    creds = {
        "access_key_id": "AKIA_STORED",
        "secret_access_key": "SECRET_STORED",
        "session_token": "TOKEN_STORED",
    }
    mock_sts = MagicMock()
    mock_eks = MagicMock()

    def _boto_client(service_name: str, **kwargs):  # type: ignore[no-untyped-def]
        if service_name == "sts":
            return mock_sts
        if service_name == "eks":
            # Assert stored creds actually propagate into the boto3 eks client.
            assert kwargs["aws_access_key_id"] == "AKIA_STORED"
            assert kwargs["aws_secret_access_key"] == "SECRET_STORED"
            assert kwargs["aws_session_token"] == "TOKEN_STORED"
            return mock_eks
        raise AssertionError(f"unexpected boto3 client: {service_name}")

    with patch("app.services.eks.eks_client.boto3.client", side_effect=_boto_client):
        EKSClient(role_arn="", region="us-east-1", credentials=creds)

    mock_sts.assume_role.assert_not_called()


def test_eks_client_stored_credentials_empty_session_token_becomes_none() -> None:
    """Empty session_token must be coerced to None before hitting boto3."""
    creds = {
        "access_key_id": "AKIA_STORED",
        "secret_access_key": "SECRET_STORED",
        "session_token": "",
    }
    captured: dict[str, object] = {}

    def _boto_client(service_name: str, **kwargs):  # type: ignore[no-untyped-def]
        if service_name == "eks":
            captured.update(kwargs)
        return MagicMock()

    with patch("app.services.eks.eks_client.boto3.client", side_effect=_boto_client):
        EKSClient(role_arn="", credentials=creds)

    assert captured["aws_session_token"] is None


def test_eks_client_falls_back_to_assume_role_when_creds_incomplete() -> None:
    """Partial stored credentials (missing access_key_id) fall back to AssumeRole."""
    creds = {"secret_access_key": "SECRET"}  # no access_key_id
    mock_sts = MagicMock()
    mock_sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "AKIA_ROLE",
            "SecretAccessKey": "SECRET_ROLE",
            "SessionToken": "TOKEN_ROLE",
        }
    }

    def _boto_client(service_name: str, **_kwargs):  # type: ignore[no-untyped-def]
        if service_name == "sts":
            return mock_sts
        return MagicMock()

    with patch("app.services.eks.eks_client.boto3.client", side_effect=_boto_client):
        EKSClient(role_arn="arn:aws:iam::123:role/r", credentials=creds)

    mock_sts.assume_role.assert_called_once()
    call_kwargs = mock_sts.assume_role.call_args.kwargs
    assert call_kwargs["RoleArn"] == "arn:aws:iam::123:role/r"


def test_eks_client_no_credentials_no_role_arn_raises() -> None:
    with (
        patch("app.services.eks.eks_client.boto3.client"),
        pytest.raises(ValueError, match="stored credentials or role_arn"),
    ):
        EKSClient(role_arn="", credentials=None)


def test_eks_client_assume_role_external_id_passed_through() -> None:
    """Existing AssumeRole path (role_arn + external_id) stays intact — no regression."""
    mock_sts = MagicMock()
    mock_sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "AK",
            "SecretAccessKey": "SK",
            "SessionToken": "TK",
        }
    }

    def _boto_client(service_name: str, **_kwargs):  # type: ignore[no-untyped-def]
        if service_name == "sts":
            return mock_sts
        return MagicMock()

    with patch("app.services.eks.eks_client.boto3.client", side_effect=_boto_client):
        EKSClient(role_arn="arn:aws:iam::123:role/r", external_id="ext-123")

    kwargs = mock_sts.assume_role.call_args.kwargs
    assert kwargs["ExternalId"] == "ext-123"
    assert kwargs["RoleSessionName"] == "TracerEKSInvestigation"
