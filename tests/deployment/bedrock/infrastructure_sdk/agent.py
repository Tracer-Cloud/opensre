"""Bedrock Agent lifecycle: create, configure action groups, prepare, invoke, and delete."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EventStreamError, ReadTimeoutError

from app.tools.registered_tool import RegisteredTool
from app.tools.registry import get_registered_tools
from tests.shared.infrastructure_sdk.deployer import get_boto3_client, wait_for_condition

logger = logging.getLogger(__name__)

DEFAULT_REGION = os.getenv("AWS_REGION", "us-east-1")
DEFAULT_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "us.anthropic.claude-sonnet-4-6",
)

BEDROCK_TOOL_NAMES: list[str] = [
    "get_cloudwatch_logs",
    "list_s3_objects",
    "get_s3_object",
    "execute_aws_operation",
    "get_lambda_errors",
    "get_sre_guidance",
]

AGENT_INSTRUCTION = (
    "You are OpenSRE, an automated SRE incident investigation agent. "
    "When given an alert or incident description, investigate the root cause by: "
    "1) Gathering evidence from logs, metrics, and cloud resources using the provided tools. "
    "2) Correlating findings across multiple data sources. "
    "3) Diagnosing the root cause with specific evidence. "
    "4) Providing a concise summary with the root cause, supporting evidence, and recommended remediation. "
    "Always explain your reasoning and cite the evidence you used."
)

MAX_RETURN_CONTROL_LOOPS = 10
INVOKE_MAX_RETRIES = 3
INVOKE_RETRY_DELAY = 5


# ---------------------------------------------------------------------------
# Tool schema conversion
# ---------------------------------------------------------------------------

_JSON_TYPE_TO_BEDROCK = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "string",
}


def _convert_parameter(
    prop: dict[str, Any],
    is_required: bool,
) -> dict[str, Any]:
    """Convert a single JSON Schema property to a Bedrock function parameter."""
    bedrock_type = _JSON_TYPE_TO_BEDROCK.get(prop.get("type", "string"), "string")
    param: dict[str, Any] = {
        "type": bedrock_type,
        "required": is_required,
    }
    description = prop.get("description", "")
    if description:
        param["description"] = description[:500]
    return param


def tool_to_bedrock_function(tool: RegisteredTool) -> dict[str, Any]:
    """Convert a RegisteredTool to a Bedrock function definition."""
    properties = tool.input_schema.get("properties", {})
    required_set = set(tool.input_schema.get("required", []))

    parameters: dict[str, Any] = {}
    for param_name, prop in properties.items():
        parameters[param_name] = _convert_parameter(prop, param_name in required_set)

    return {
        "name": tool.name,
        "description": (tool.description or tool.name)[:1200],
        "parameters": parameters,
    }


def tools_to_bedrock_functions(
    tools: list[RegisteredTool],
) -> list[dict[str, Any]]:
    """Convert a list of RegisteredTools to Bedrock function definitions."""
    return [tool_to_bedrock_function(t) for t in tools]


def get_bedrock_tools() -> list[RegisteredTool]:
    """Return the subset of registered tools exposed to Bedrock."""
    all_tools = get_registered_tools("investigation")
    name_set = set(BEDROCK_TOOL_NAMES)
    selected = [t for t in all_tools if t.name in name_set]
    if not selected:
        logger.warning("No matching tools found for BEDROCK_TOOL_NAMES; using first 5 tools")
        selected = all_tools[:5]
    return selected


# ---------------------------------------------------------------------------
# Agent CRUD
# ---------------------------------------------------------------------------


def create_agent(
    agent_name: str,
    role_arn: str,
    *,
    model_id: str = DEFAULT_MODEL_ID,
    instruction: str = AGENT_INSTRUCTION,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Create a Bedrock Agent.

    Returns:
        Dict with agentId, agentArn, agentVersion.
    """
    client = get_boto3_client("bedrock-agent", region)

    response = client.create_agent(
        agentName=agent_name,
        agentResourceRoleArn=role_arn,
        foundationModel=model_id,
        instruction=instruction,
        idleSessionTTLInSeconds=600,
    )

    agent = response["agent"]
    return {
        "agentId": agent["agentId"],
        "agentArn": agent["agentArn"],
        "agentVersion": "DRAFT",
    }


def create_action_group(
    agent_id: str,
    agent_version: str,
    functions: list[dict[str, Any]],
    *,
    group_name: str = "opensre-tools",
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Create an action group with RETURN_CONTROL execution.

    Returns:
        Dict with actionGroupId.
    """
    client = get_boto3_client("bedrock-agent", region)

    response = client.create_agent_action_group(
        agentId=agent_id,
        agentVersion=agent_version,
        actionGroupName=group_name,
        actionGroupExecutor={"customControl": "RETURN_CONTROL"},
        functionSchema={"functions": functions},
    )

    ag = response["agentActionGroup"]
    return {"actionGroupId": ag["actionGroupId"]}


def prepare_agent(agent_id: str, *, region: str = DEFAULT_REGION) -> None:
    """Prepare the agent and block until status is PREPARED."""
    client = get_boto3_client("bedrock-agent", region)
    client.prepare_agent(agentId=agent_id)

    def _is_prepared() -> bool:
        resp = client.get_agent(agentId=agent_id)
        status = resp["agent"]["agentStatus"]
        if status == "FAILED":
            reasons = resp["agent"].get("failureReasons", [])
            raise RuntimeError(f"Agent preparation failed: {reasons}")
        return status == "PREPARED"

    wait_for_condition(_is_prepared, max_attempts=60, delay_seconds=5, description="agent PREPARED")


def create_alias(
    agent_id: str,
    *,
    alias_name: str = "e2e-test",
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Create an agent alias pointing at the latest prepared version.

    Returns:
        Dict with agentAliasId.
    """
    client = get_boto3_client("bedrock-agent", region)

    response = client.create_agent_alias(
        agentId=agent_id,
        agentAliasName=alias_name,
    )

    alias_id = response["agentAlias"]["agentAliasId"]

    def _alias_ready() -> bool:
        resp = client.get_agent_alias(agentId=agent_id, agentAliasId=alias_id)
        return resp["agentAlias"]["agentAliasStatus"] == "PREPARED"

    wait_for_condition(_alias_ready, max_attempts=30, delay_seconds=3, description="alias PREPARED")

    return {"agentAliasId": alias_id}


# ---------------------------------------------------------------------------
# Agent invocation with RETURN_CONTROL loop
# ---------------------------------------------------------------------------


def invoke_agent_with_tool_loop(
    agent_id: str,
    alias_id: str,
    input_text: str,
    tool_map: dict[str, RegisteredTool],
    *,
    session_id: str | None = None,
    region: str = DEFAULT_REGION,
    max_loops: int = MAX_RETURN_CONTROL_LOOPS,
) -> str:
    """Invoke a Bedrock Agent and handle RETURN_CONTROL tool execution locally.

    Args:
        agent_id: Bedrock Agent ID.
        alias_id: Bedrock Agent alias ID.
        input_text: The user prompt.
        tool_map: Mapping of tool name -> RegisteredTool for local execution.
        session_id: Optional session ID for conversation continuity.
        region: AWS region.
        max_loops: Max RETURN_CONTROL iterations before giving up.

    Returns:
        Final text response from the agent.
    """
    runtime_config = Config(
        retries={"max_attempts": 3, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=120,
    )
    client = boto3.client("bedrock-agent-runtime", region_name=region, config=runtime_config)
    session_id = session_id or str(uuid.uuid4())

    collected_text: list[str] = []

    invoke_kwargs: dict[str, Any] = {
        "agentId": agent_id,
        "agentAliasId": alias_id,
        "sessionId": session_id,
        "inputText": input_text,
    }

    for iteration in range(max_loops):
        last_error: Exception | None = None
        for attempt in range(INVOKE_MAX_RETRIES):
            try:
                response = client.invoke_agent(**invoke_kwargs)
                return_control: dict[str, Any] | None = None

                for event in response["completion"]:
                    if "chunk" in event:
                        chunk_bytes = event["chunk"].get("bytes", b"")
                        collected_text.append(chunk_bytes.decode("utf-8", errors="replace"))
                    elif "returnControl" in event:
                        return_control = event["returnControl"]

                last_error = None
                break
            except (EventStreamError, ClientError, ReadTimeoutError) as exc:
                err_code = ""
                if isinstance(exc, ReadTimeoutError):
                    err_code = "ReadTimeoutError"
                elif isinstance(exc, ClientError):
                    err_code = exc.response.get("Error", {}).get("Code", "")
                elif "dependencyFailed" in str(exc) or "throttl" in str(exc).lower():
                    err_code = "dependencyFailedException"

                if err_code in (
                    "dependencyFailedException",
                    "ThrottlingException",
                    "ServiceUnavailableException",
                    "ReadTimeoutError",
                ):
                    last_error = exc
                    wait = INVOKE_RETRY_DELAY * (attempt + 1)
                    logger.warning(
                        "Transient error on attempt %d/%d, retrying in %ds: %s",
                        attempt + 1,
                        INVOKE_MAX_RETRIES,
                        wait,
                        exc,
                    )
                    time.sleep(wait)
                else:
                    raise

        if last_error is not None:
            raise last_error

        if return_control is None:
            break

        invocation_id = return_control["invocationId"]
        invocation_inputs = return_control.get("invocationInputs", [])

        return_control_results: list[dict[str, Any]] = []

        for invocation_input in invocation_inputs:
            func_input = invocation_input.get("functionInvocationInput", {})
            action_group = func_input.get("actionGroup", "")
            function_name = func_input.get("function", "")
            parameters = func_input.get("parameters", [])

            kwargs = {p["name"]: p.get("value", "") for p in parameters}

            logger.info(
                "RETURN_CONTROL iteration %d: %s(%s)",
                iteration,
                function_name,
                list(kwargs.keys()),
            )

            tool = tool_map.get(function_name)
            if tool is not None:
                try:
                    result = tool.run(**kwargs)
                    result_body = json.dumps(result, default=str)[:25000]
                except Exception as exc:
                    result_body = json.dumps({"error": str(exc)})
            else:
                result_body = json.dumps({"error": f"Unknown tool: {function_name}"})

            return_control_results.append(
                {
                    "functionResult": {
                        "actionGroup": action_group,
                        "function": function_name,
                        "responseBody": {"TEXT": {"body": result_body}},
                    }
                }
            )

        invoke_kwargs = {
            "agentId": agent_id,
            "agentAliasId": alias_id,
            "sessionId": session_id,
            "sessionState": {
                "invocationId": invocation_id,
                "returnControlInvocationResults": return_control_results,
            },
        }

    return "".join(collected_text)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def delete_agent(
    agent_id: str,
    alias_id: str | None = None,
    *,
    region: str = DEFAULT_REGION,
) -> None:
    """Delete an agent and optionally its alias."""
    client = get_boto3_client("bedrock-agent", region)

    if alias_id:
        try:
            client.delete_agent_alias(agentId=agent_id, agentAliasId=alias_id)
            time.sleep(2)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise

    try:
        client.delete_agent(agentId=agent_id, skipResourceInUseCheck=True)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
