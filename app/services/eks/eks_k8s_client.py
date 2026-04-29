"""Kubernetes client for EKS — builds in-memory config using STS presigned token.

Programmatic equivalent of `aws eks get-token` — no kubectl binary required.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
import weakref
from typing import Any

import boto3
import botocore.auth
import botocore.awsrequest
import botocore.credentials
import botocore.session
from kubernetes import client as k8s_client

from app.services.eks.utils import stored_credentials_to_aws_creds

logger = logging.getLogger(__name__)


def _assume_role(role_arn: str, external_id: str, session_name: str) -> dict[str, Any]:
    logger.info("[eks] Assuming role: %s (external_id=%s)", role_arn, external_id or "none")
    sts = boto3.client("sts")
    kwargs: dict = {"RoleArn": role_arn, "RoleSessionName": session_name}
    if external_id:
        kwargs["ExternalId"] = external_id
    creds: dict[str, Any] = sts.assume_role(**kwargs)["Credentials"]
    logger.info("[eks] AssumeRole OK — AccessKeyId prefix: %s", creds["AccessKeyId"][:8])
    return creds


def _generate_eks_token(cluster_name: str, assumed_creds: dict[str, Any], region: str) -> str:
    """Generate EKS bearer token equivalent to `aws eks get-token`.

    Builds a SigV4-signed presigned URL for STS GetCallerIdentity with the
    x-k8s-aws-id header included in the canonical request before signing.
    This matches exactly what aws-iam-authenticator and `aws eks get-token` do.
    """
    creds = botocore.credentials.Credentials(
        access_key=assumed_creds["AccessKeyId"],
        secret_key=assumed_creds["SecretAccessKey"],
        token=assumed_creds["SessionToken"] or None,
    )

    sts_url = f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15"
    request = botocore.awsrequest.AWSRequest(
        method="GET",
        url=sts_url,
        headers={"x-k8s-aws-id": cluster_name},
    )

    signer = botocore.auth.SigV4QueryAuth(creds, "sts", region, expires=60)
    signer.add_auth(request)

    signed_url = request.url
    if signed_url is None:
        msg = "Failed to generate presigned STS URL for EKS token"
        logger.error("[eks] %s", msg)
        raise RuntimeError(msg)

    token = "k8s-aws-v1." + base64.urlsafe_b64encode(signed_url.encode()).decode().rstrip("=")
    logger.info("[eks] Token generated for cluster=%s (length=%d)", cluster_name, len(token))
    return token


def _delete_temp_cert(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


def build_k8s_clients(
    cluster_name: str,
    role_arn: str,
    external_id: str,
    region: str,
    credentials: dict[str, Any] | None = None,
) -> tuple[k8s_client.CoreV1Api, k8s_client.AppsV1Api]:
    """Assume role, describe cluster, build in-memory Kubernetes API clients.

    Credential resolution priority:
      1. Explicit `credentials` kwarg (stored AWS integration IAM user creds).
      2. `role_arn` → STS AssumeRole (existing behaviour).
      3. Ambient botocore chain (env AK/SK, shared config, instance profile / IRSA).

    Returns (CoreV1Api, AppsV1Api) ready to query pods, events, nodes, deployments.
    No kubeconfig file is written to disk.
    """
    stored = stored_credentials_to_aws_creds(credentials)
    if stored is not None:
        # Explicit stored-integration credentials path (highest priority).
        # Matches the catalog + resolve_integrations flow: the AWS integration
        # is configured with IAM user creds (access_key_id + secret_access_key),
        # possibly with a session_token, and no role_arn. Previously these
        # silently fell through to the ambient botocore chain which missed
        # the stored values entirely when ambient creds were also set but
        # pointed elsewhere. The shared ``stored_credentials_to_aws_creds``
        # helper keeps the normalization rules (empty session_token → None,
        # both required keys present) in sync with ``EKSClient``.
        logger.info("[eks] Using explicit stored-integration AWS credentials")
        assumed = stored
    elif role_arn:
        assumed = _assume_role(role_arn, external_id, "TracerEKSK8sInvestigation")
    else:
        # No role_arn and no explicit creds: fall back to ambient AWS
        # credentials (env AK/SK, shared config profile, or instance profile
        # / IRSA). Preserves the #724 fallback behaviour.
        logger.info("[eks] No role_arn or explicit credentials; using ambient AWS credentials")
        sess = botocore.session.get_session()
        ambient = sess.get_credentials()
        if ambient is None:
            msg = "No AWS credentials available for EKS investigation"
            logger.error("[eks] %s", msg)
            raise RuntimeError(msg)
        frozen = ambient.get_frozen_credentials()
        assumed = {
            "AccessKeyId": frozen.access_key,
            "SecretAccessKey": frozen.secret_key,
            "SessionToken": frozen.token or None,
        }

    logger.info("[eks] Describing cluster: %s in region %s", cluster_name, region)
    eks = boto3.client(
        "eks",
        region_name=region,
        aws_access_key_id=assumed["AccessKeyId"],
        aws_secret_access_key=assumed["SecretAccessKey"],
        aws_session_token=assumed["SessionToken"] or None,
    )
    cluster_info = eks.describe_cluster(name=cluster_name)["cluster"]
    endpoint = cluster_info["endpoint"]
    status = cluster_info.get("status")
    k8s_version = cluster_info.get("version")
    ca_data = cluster_info["certificateAuthority"]["data"]
    logger.info(
        "[eks] Cluster %s — status=%s version=%s endpoint=%s",
        cluster_name,
        status,
        k8s_version,
        endpoint,
    )

    ca_bytes = base64.b64decode(ca_data)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".crt") as ca_file:
        ca_file.write(ca_bytes)
        ca_file.flush()
        ca_path = ca_file.name
    logger.info("[eks] CA cert written to %s", ca_path)

    token = _generate_eks_token(cluster_name, assumed, region)

    configuration = k8s_client.Configuration()
    configuration.host = endpoint
    configuration.ssl_ca_cert = ca_path
    configuration.api_key = {"authorization": f"Bearer {token}"}

    logger.info("[eks] K8s client built — host=%s", endpoint)
    try:
        api_client = k8s_client.ApiClient(configuration)
    except Exception:
        _delete_temp_cert(ca_path)
        raise
    api_client._ca_cert_cleanup = weakref.finalize(api_client, _delete_temp_cert, ca_path)
    return k8s_client.CoreV1Api(api_client), k8s_client.AppsV1Api(api_client)
