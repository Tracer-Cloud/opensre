"""Tests for app.services.eks.eks_k8s_client.

Focus on the credential-resolution path in ``build_k8s_clients``: stored
integration credentials must take priority over ``role_arn`` AssumeRole.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.eks import eks_k8s_client
from app.services.eks.eks_k8s_client import (
    _stored_credentials_to_aws_creds,
    build_k8s_clients,
)

# ---------------------------------------------------------------------------
# _stored_credentials_to_aws_creds — pure helper, no AWS
# ---------------------------------------------------------------------------


def test_stored_credentials_full_dict_returns_assume_role_shape() -> None:
    out = _stored_credentials_to_aws_creds(
        {
            "access_key_id": "AKIATEST",
            "secret_access_key": "secret",
            "session_token": "token",
        }
    )
    assert out == {
        "AccessKeyId": "AKIATEST",
        "SecretAccessKey": "secret",
        "SessionToken": "token",
    }


def test_stored_credentials_empty_session_token_coerced_to_none() -> None:
    """IAM user keys typically have no session token. Empty string must become
    None — botocore rejects empty SessionToken but accepts a missing one."""
    out = _stored_credentials_to_aws_creds(
        {
            "access_key_id": "AKIATEST",
            "secret_access_key": "secret",
            "session_token": "",
        }
    )
    assert out is not None
    assert out["SessionToken"] is None


def test_stored_credentials_missing_session_token_coerced_to_none() -> None:
    out = _stored_credentials_to_aws_creds(
        {"access_key_id": "AKIATEST", "secret_access_key": "secret"}
    )
    assert out is not None
    assert out["SessionToken"] is None


@pytest.mark.parametrize(
    "creds",
    [
        {},
        {"access_key_id": "AKIATEST"},
        {"secret_access_key": "secret"},
        {"access_key_id": "", "secret_access_key": "secret"},
        {"access_key_id": "AKIATEST", "secret_access_key": ""},
    ],
)
def test_stored_credentials_returns_none_when_required_keys_missing(
    creds: dict,
) -> None:
    assert _stored_credentials_to_aws_creds(creds) is None


# ---------------------------------------------------------------------------
# build_k8s_clients — credential resolution priority
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_eks_describe() -> MagicMock:
    """Stub the boto3 EKS client so describe_cluster returns a usable shape."""
    eks_mock = MagicMock()
    eks_mock.describe_cluster.return_value = {
        "cluster": {
            "endpoint": "https://example.eks.amazonaws.com",
            "status": "ACTIVE",
            "version": "1.29",
            # base64 of b"fake-cert"
            "certificateAuthority": {"data": "ZmFrZS1jZXJ0"},
        }
    }
    return eks_mock


def test_build_k8s_clients_uses_stored_credentials_when_provided(
    mock_eks_describe: MagicMock,
) -> None:
    """Stored credentials must be the highest-priority resolution path —
    AssumeRole must not be called when they are supplied."""
    with (
        patch.object(eks_k8s_client.boto3, "client", return_value=mock_eks_describe) as boto_client,
        patch.object(eks_k8s_client, "_assume_role") as assume_role,
        patch.object(eks_k8s_client, "_generate_eks_token", return_value="k8s-aws-v1.token"),
    ):
        core, apps = build_k8s_clients(
            cluster_name="c1",
            role_arn="arn:aws:iam::123:role/r",
            external_id="",
            region="us-east-1",
            credentials={
                "access_key_id": "AKIASTORED",
                "secret_access_key": "secret",
                "session_token": "",
            },
        )

    assume_role.assert_not_called()
    boto_client.assert_called_once()
    _, kwargs = boto_client.call_args
    assert kwargs["aws_access_key_id"] == "AKIASTORED"
    assert kwargs["aws_secret_access_key"] == "secret"
    assert kwargs["aws_session_token"] is None
    assert core is not None
    assert apps is not None


def test_build_k8s_clients_falls_back_to_assume_role_when_no_credentials(
    mock_eks_describe: MagicMock,
) -> None:
    with (
        patch.object(eks_k8s_client.boto3, "client", return_value=mock_eks_describe),
        patch.object(
            eks_k8s_client,
            "_assume_role",
            return_value={
                "AccessKeyId": "AKIAASSUMED",
                "SecretAccessKey": "assumed-secret",
                "SessionToken": "assumed-token",
            },
        ) as assume_role,
        patch.object(eks_k8s_client, "_generate_eks_token", return_value="k8s-aws-v1.token"),
    ):
        build_k8s_clients(
            cluster_name="c1",
            role_arn="arn:aws:iam::123:role/r",
            external_id="ext",
            region="us-west-2",
        )

    assume_role.assert_called_once_with(
        "arn:aws:iam::123:role/r", "ext", "TracerEKSK8sInvestigation"
    )


def test_build_k8s_clients_falls_back_to_assume_role_when_credentials_incomplete(
    mock_eks_describe: MagicMock,
) -> None:
    """A credentials dict that lacks the IAM user keys must not block the
    AssumeRole fallback — partially configured integrations should still work."""
    with (
        patch.object(eks_k8s_client.boto3, "client", return_value=mock_eks_describe),
        patch.object(
            eks_k8s_client,
            "_assume_role",
            return_value={
                "AccessKeyId": "AKIAASSUMED",
                "SecretAccessKey": "assumed-secret",
                "SessionToken": "assumed-token",
            },
        ) as assume_role,
        patch.object(eks_k8s_client, "_generate_eks_token", return_value="k8s-aws-v1.token"),
    ):
        build_k8s_clients(
            cluster_name="c1",
            role_arn="arn:aws:iam::123:role/r",
            external_id="",
            region="us-east-1",
            credentials={"access_key_id": "AKIATEST"},  # missing secret
        )

    assume_role.assert_called_once()
