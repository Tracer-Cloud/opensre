"""AWS SDK-based infrastructure deployer.

This module provides reusable resource creators using boto3 directly,
avoiding CDK for faster deployment cycles during testing.
"""

from tests.shared.infrastructure_sdk.cleanup import (
    destroy_stack,
    find_resources_by_stack,
)
from tests.shared.infrastructure_sdk.config import (
    get_output,
    load_outputs,
    save_outputs,
)
from tests.shared.infrastructure_sdk.deployer import (
    get_boto3_client,
    get_standard_tags,
    run_parallel,
)

__all__ = [
    "get_boto3_client",
    "get_standard_tags",
    "run_parallel",
    "save_outputs",
    "load_outputs",
    "get_output",
    "destroy_stack",
    "find_resources_by_stack",
]
