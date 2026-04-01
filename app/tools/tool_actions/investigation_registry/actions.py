"""Registry of all available investigation actions."""

import logging

from app.tools.tool_actions.base import BaseTool

logger = logging.getLogger(__name__)


def get_available_actions() -> list[BaseTool]:
    """Return all registered investigation tools.

    Each tool lives in its own PascalCase package under ``tool_actions/``.
    Adding a new tool requires creating a ``BaseTool`` subclass in a new
    package — no edits needed here.
    """
    from app.tools.tool_actions.AWSOperationTool import AWSOperationTool
    from app.tools.tool_actions.CloudWatchBatchMetricsTool import CloudWatchBatchMetricsTool
    from app.tools.tool_actions.CloudWatchLogsTool import CloudWatchLogsTool
    from app.tools.tool_actions.DataDogContextTool import DataDogContextTool
    from app.tools.tool_actions.DataDogEventsTool import DataDogEventsTool
    from app.tools.tool_actions.DataDogLogsTool import DataDogLogsTool
    from app.tools.tool_actions.DataDogMetricsTool import DataDogMetricsTool
    from app.tools.tool_actions.DataDogMonitorsTool import DataDogMonitorsTool
    from app.tools.tool_actions.DataDogNodePodsTool import DataDogNodePodsTool
    from app.tools.tool_actions.GitHubCommitsTool import GitHubCommitsTool
    from app.tools.tool_actions.GitHubFileContentsTool import GitHubFileContentsTool
    from app.tools.tool_actions.GitHubRepositoryTreeTool import GitHubRepositoryTreeTool
    from app.tools.tool_actions.GitHubSearchCodeTool import GitHubSearchCodeTool
    from app.tools.tool_actions.GrafanaAlertRulesTool import GrafanaAlertRulesTool
    from app.tools.tool_actions.GrafanaLogsTool import GrafanaLogsTool
    from app.tools.tool_actions.GrafanaMetricsTool import GrafanaMetricsTool
    from app.tools.tool_actions.GrafanaServiceNamesTool import GrafanaServiceNamesTool
    from app.tools.tool_actions.GrafanaTracesTool import GrafanaTracesTool
    from app.tools.tool_actions.LambdaConfigTool import LambdaConfigTool
    from app.tools.tool_actions.LambdaErrorsTool import LambdaErrorsTool
    from app.tools.tool_actions.LambdaInspectTool import LambdaInspectTool
    from app.tools.tool_actions.LambdaInvocationLogsTool import LambdaInvocationLogsTool
    from app.tools.tool_actions.S3GetObjectTool import S3GetObjectTool
    from app.tools.tool_actions.S3InspectTool import S3InspectTool
    from app.tools.tool_actions.S3ListTool import S3ListTool
    from app.tools.tool_actions.S3MarkerTool import S3MarkerTool
    from app.tools.tool_actions.SentryIssueDetailsTool import SentryIssueDetailsTool
    from app.tools.tool_actions.SentryIssueEventsTool import SentryIssueEventsTool
    from app.tools.tool_actions.SentrySearchIssuesTool import SentrySearchIssuesTool
    from app.tools.tool_actions.SREGuidanceTool import SREGuidanceTool
    from app.tools.tool_actions.TracerAirflowMetricsTool import TracerAirflowMetricsTool
    from app.tools.tool_actions.TracerBatchStatisticsTool import TracerBatchStatisticsTool
    from app.tools.tool_actions.TracerErrorLogsTool import TracerErrorLogsTool
    from app.tools.tool_actions.TracerFailedJobsTool import TracerFailedJobsTool
    from app.tools.tool_actions.TracerFailedRunTool import TracerFailedRunTool
    from app.tools.tool_actions.TracerFailedToolsTool import TracerFailedToolsTool
    from app.tools.tool_actions.TracerHostMetricsTool import TracerHostMetricsTool
    from app.tools.tool_actions.TracerRunTool import TracerRunTool
    from app.tools.tool_actions.TracerTasksTool import TracerTasksTool

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
        from app.tools.tool_actions.EKSDeploymentStatusTool import EKSDeploymentStatusTool
        from app.tools.tool_actions.EKSDescribeAddonTool import EKSDescribeAddonTool
        from app.tools.tool_actions.EKSDescribeClusterTool import EKSDescribeClusterTool
        from app.tools.tool_actions.EKSEventsTool import EKSEventsTool
        from app.tools.tool_actions.EKSListClustersTool import EKSListClustersTool
        from app.tools.tool_actions.EKSListDeploymentsTool import EKSListDeploymentsTool
        from app.tools.tool_actions.EKSListNamespacesTool import EKSListNamespacesTool
        from app.tools.tool_actions.EKSListPodsTool import EKSListPodsTool
        from app.tools.tool_actions.EKSNodegroupHealthTool import EKSNodegroupHealthTool
        from app.tools.tool_actions.EKSNodeHealthTool import EKSNodeHealthTool
        from app.tools.tool_actions.EKSPodLogsTool import EKSPodLogsTool

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
