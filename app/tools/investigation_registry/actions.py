"""Registry of all available investigation actions."""

import logging

from app.tools.base import BaseTool

logger = logging.getLogger(__name__)


def get_available_actions() -> list[BaseTool]:
    """Return all registered investigation tools.

    Each tool lives in its own PascalCase package under ``app/tools/``.
    Adding a new tool requires creating a ``BaseTool`` subclass in a new
    package — no edits needed here.
    """
    from app.tools.AWSOperationTool import AWSOperationTool
    from app.tools.CloudWatchBatchMetricsTool import CloudWatchBatchMetricsTool
    from app.tools.CloudWatchLogsTool import CloudWatchLogsTool
    from app.tools.DataDogContextTool import DataDogContextTool
    from app.tools.DataDogEventsTool import DataDogEventsTool
    from app.tools.DataDogLogsTool import DataDogLogsTool
    from app.tools.DataDogMetricsTool import DataDogMetricsTool
    from app.tools.DataDogMonitorsTool import DataDogMonitorsTool
    from app.tools.DataDogNodePodsTool import DataDogNodePodsTool
    from app.tools.GitHubCommitsTool import GitHubCommitsTool
    from app.tools.GitHubFileContentsTool import GitHubFileContentsTool
    from app.tools.GitHubRepositoryTreeTool import GitHubRepositoryTreeTool
    from app.tools.GitHubSearchCodeTool import GitHubSearchCodeTool
    from app.tools.GrafanaAlertRulesTool import GrafanaAlertRulesTool
    from app.tools.GrafanaLogsTool import GrafanaLogsTool
    from app.tools.GrafanaMetricsTool import GrafanaMetricsTool
    from app.tools.GrafanaServiceNamesTool import GrafanaServiceNamesTool
    from app.tools.GrafanaTracesTool import GrafanaTracesTool
    from app.tools.LambdaConfigTool import LambdaConfigTool
    from app.tools.LambdaErrorsTool import LambdaErrorsTool
    from app.tools.LambdaInspectTool import LambdaInspectTool
    from app.tools.LambdaInvocationLogsTool import LambdaInvocationLogsTool
    from app.tools.S3GetObjectTool import S3GetObjectTool
    from app.tools.S3InspectTool import S3InspectTool
    from app.tools.S3ListTool import S3ListTool
    from app.tools.S3MarkerTool import S3MarkerTool
    from app.tools.SentryIssueDetailsTool import SentryIssueDetailsTool
    from app.tools.SentryIssueEventsTool import SentryIssueEventsTool
    from app.tools.SentrySearchIssuesTool import SentrySearchIssuesTool
    from app.tools.SREGuidanceTool import SREGuidanceTool
    from app.tools.TracerAirflowMetricsTool import TracerAirflowMetricsTool
    from app.tools.TracerBatchStatisticsTool import TracerBatchStatisticsTool
    from app.tools.TracerErrorLogsTool import TracerErrorLogsTool
    from app.tools.TracerFailedJobsTool import TracerFailedJobsTool
    from app.tools.TracerFailedRunTool import TracerFailedRunTool
    from app.tools.TracerFailedToolsTool import TracerFailedToolsTool
    from app.tools.TracerHostMetricsTool import TracerHostMetricsTool
    from app.tools.TracerRunTool import TracerRunTool
    from app.tools.TracerTasksTool import TracerTasksTool

    tools: list[BaseTool] = [
        DataDogLogsTool(),
        DataDogEventsTool(),
        DataDogMonitorsTool(),
        DataDogContextTool(),
        DataDogNodePodsTool(),
        DataDogMetricsTool(),
        GrafanaLogsTool(),
        GrafanaTracesTool(),
        GrafanaMetricsTool(),
        GrafanaAlertRulesTool(),
        GrafanaServiceNamesTool(),
        CloudWatchLogsTool(),
        CloudWatchBatchMetricsTool(),
        S3MarkerTool(),
        S3InspectTool(),
        S3ListTool(),
        S3GetObjectTool(),
        LambdaInvocationLogsTool(),
        LambdaErrorsTool(),
        LambdaInspectTool(),
        LambdaConfigTool(),
        AWSOperationTool(),
        TracerFailedJobsTool(),
        TracerFailedToolsTool(),
        TracerErrorLogsTool(),
        TracerHostMetricsTool(),
        TracerBatchStatisticsTool(),
        TracerAirflowMetricsTool(),
        TracerFailedRunTool(),
        TracerRunTool(),
        TracerTasksTool(),
        GitHubSearchCodeTool(),
        GitHubFileContentsTool(),
        GitHubRepositoryTreeTool(),
        GitHubCommitsTool(),
        SentrySearchIssuesTool(),
        SentryIssueDetailsTool(),
        SentryIssueEventsTool(),
        SREGuidanceTool(),
    ]

    try:
        from app.tools.EKSDeploymentStatusTool import EKSDeploymentStatusTool
        from app.tools.EKSDescribeAddonTool import EKSDescribeAddonTool
        from app.tools.EKSDescribeClusterTool import EKSDescribeClusterTool
        from app.tools.EKSEventsTool import EKSEventsTool
        from app.tools.EKSListClustersTool import EKSListClustersTool
        from app.tools.EKSListDeploymentsTool import EKSListDeploymentsTool
        from app.tools.EKSListNamespacesTool import EKSListNamespacesTool
        from app.tools.EKSListPodsTool import EKSListPodsTool
        from app.tools.EKSNodegroupHealthTool import EKSNodegroupHealthTool
        from app.tools.EKSNodeHealthTool import EKSNodeHealthTool
        from app.tools.EKSPodLogsTool import EKSPodLogsTool

        tools.extend([
            EKSListClustersTool(),
            EKSDescribeClusterTool(),
            EKSNodegroupHealthTool(),
            EKSDescribeAddonTool(),
            EKSPodLogsTool(),
            EKSListPodsTool(),
            EKSEventsTool(),
            EKSDeploymentStatusTool(),
            EKSListDeploymentsTool(),
            EKSNodeHealthTool(),
            EKSListNamespacesTool(),
        ])
    except ModuleNotFoundError as exc:
        logger.warning("[actions] EKS actions unavailable: %s", exc)

    return tools
