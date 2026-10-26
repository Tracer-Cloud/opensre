"""RDS instance description tool — backed by aws_sdk_client."""

from __future__ import annotations

import logging
from typing import Any, cast

from pydantic import BaseModel, Field

from core.domain.types.evidence import record_evidence_entry
from core.tool import EvidenceType, SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.aws.aws_sdk_client import execute_aws_sdk_call
from integrations.rds import (
    DEFAULT_RDS_REGION,
    rds_extract_params,
    rds_is_available,
)

logger = logging.getLogger(__name__)


def _map_describe_rds_instance(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the described RDS instance's status/engine as report evidence."""
    if not output.get("available"):
        return
    status = output.get("status")
    engine = output.get("engine")
    engine_version = output.get("engine_version")
    parts = [
        part
        for part in (
            status,
            f"{engine} {engine_version}".strip() if engine else None,
        )
        if part
    ]
    if not parts:
        return
    record_evidence_entry(
        evidence,
        source="describe_rds_instance",
        label="RDS Instance",
        summary=", ".join(parts),
    )


class DescribeRDSInstanceInput(BaseModel):
    db_instance_identifier: str = Field(
        description="RDS DB instance identifier, for example `prod-orders-db`."
    )
    region: str = Field(
        default=DEFAULT_RDS_REGION,
        description="AWS region where the RDS instance is deployed.",
    )


class DescribeRDSInstanceOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether instance metadata could be retrieved.")
    db_instance_identifier: str = Field(description="Queried RDS instance identifier.")
    status: str | None = Field(default=None, description="Current DB instance lifecycle status.")
    engine: str | None = Field(default=None, description="Database engine name.")
    engine_version: str | None = Field(default=None, description="Database engine version.")
    instance_class: str | None = Field(default=None, description="Instance class size.")
    multi_az: bool | None = Field(default=None, description="Whether Multi-AZ is enabled.")
    publicly_accessible: bool | None = Field(default=None, description="Public accessibility flag.")
    storage_type: str | None = Field(default=None, description="RDS storage type.")
    allocated_storage_gb: int | None = Field(default=None, description="Allocated storage in GiB.")
    endpoint: dict[str, Any] | None = Field(default=None, description="Database endpoint details.")
    availability_zone: str | None = Field(default=None, description="Availability zone placement.")
    preferred_backup_window: str | None = Field(
        default=None, description="Configured backup window."
    )
    backup_retention_period: int | None = Field(
        default=None, description="Backup retention period in days."
    )
    error: str | None = Field(default=None, description="Error details when lookup fails.")


@tool(
    name="describe_rds_instance",
    source="rds",
    description=(
        "Describe an AWS RDS database instance — engine, version, status, "
        "storage, Multi-AZ, endpoint, and parameter groups."
    ),
    use_cases=[
        "Investigating instance-level issues: status, availability, engine version",
        "Checking if Multi-AZ is enabled or storage is misconfigured",
        "Verifying RDS instance status (available, modifying, failed)",
    ],
    requires=["db_instance_identifier"],
    source_id="aws_rds",
    evidence_type=EvidenceType.DEPLOYMENT_METADATA,
    side_effect_level=SideEffectLevel.READ_ONLY,
    examples=[
        "Describe `prod-orders-db` to confirm if status is `modifying` during an incident.",
        "Check engine version and backup configuration before rollback decisions.",
    ],
    anti_examples=["Use this tool to inspect SQL query text or Postgres locks."],
    input_model=DescribeRDSInstanceInput,
    output_model=DescribeRDSInstanceOutput,
    injected_params=("aws_backend",),
    is_available=rds_is_available,
    extract_params=rds_extract_params,
    evidence_mapper=_map_describe_rds_instance,
)
def describe_rds_instance(
    db_instance_identifier: str,
    region: str = DEFAULT_RDS_REGION,
    aws_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Describe an RDS instance — status, engine, storage, networking.

    When ``aws_backend`` is provided (FixtureAWSBackend in synthetic tests)
    the call short-circuits to the backend so we never leak boto3 calls to
    real AWS during scenario runs. Otherwise calls boto3 rds via
    ``execute_aws_sdk_call`` using the default boto3 credential chain.
    """
    logger.info(
        "[rds] describe_rds_instance db=%s region=%s",
        db_instance_identifier,
        region,
    )

    if aws_backend is not None:
        return cast(
            "dict[str, Any]",
            aws_backend.describe_db_instances(
                db_instance_identifier=db_instance_identifier,
                region=region,
            ),
        )

    result = execute_aws_sdk_call(
        service_name="rds",
        operation_name="describe_db_instances",
        parameters={"DBInstanceIdentifier": db_instance_identifier},
        region=region,
    )

    if not result.get("success"):
        logger.error(
            "[rds] describe_db_instances failed for db=%s region=%s: %s",
            db_instance_identifier,
            region,
            result.get("error"),
        )
        return tool_unavailable(
            "rds",
            "Failed to describe the RDS instance. Check server logs for details.",
            db_instance_identifier=db_instance_identifier,
        )

    instances = (result.get("data") or {}).get("DBInstances") or []
    if not instances:
        return tool_unavailable(
            "rds",
            "No RDS instance found with the given identifier.",
            db_instance_identifier=db_instance_identifier,
        )

    if len(instances) > 1:
        logger.warning(
            "[rds] describe_db_instances returned %d instances for db=%s; "
            "using the first result only.",
            len(instances),
            db_instance_identifier,
        )

    instance = instances[0]
    endpoint = instance.get("Endpoint") or {}

    return {
        "source": "rds",
        "available": True,
        "db_instance_identifier": db_instance_identifier,
        "status": instance.get("DBInstanceStatus"),
        "engine": instance.get("Engine"),
        "engine_version": instance.get("EngineVersion"),
        "instance_class": instance.get("DBInstanceClass"),
        "multi_az": instance.get("MultiAZ"),
        "publicly_accessible": instance.get("PubliclyAccessible"),
        "storage_type": instance.get("StorageType"),
        "allocated_storage_gb": instance.get("AllocatedStorage"),
        "endpoint": {
            "address": endpoint.get("Address"),
            "port": endpoint.get("Port"),
        },
        "availability_zone": instance.get("AvailabilityZone"),
        "preferred_backup_window": instance.get("PreferredBackupWindow"),
        "backup_retention_period": instance.get("BackupRetentionPeriod"),
        "error": None,
    }
