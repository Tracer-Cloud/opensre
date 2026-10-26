"""Shared helpers for the Amazon Bedrock Converse API (tool schemas and messages)."""

from __future__ import annotations

import json
import logging
import os
import secrets
from collections.abc import Sequence
from typing import Any

from core.llm.shared.tool_schema_normalize import (
    BEDROCK_UNSUPPORTED_SCHEMA_KEYS,
    normalize_object_tool_input_schema,
)
from core.llm.types import SchemaDescribedTool

logger = logging.getLogger(__name__)


def require_aws_region() -> str:
    """Return configured AWS region or raise with a clear configuration error."""
    region = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()
    if not region:
        raise RuntimeError("Bedrock requires AWS_REGION or AWS_DEFAULT_REGION to be set.")
    return region


def new_tool_use_id() -> str:
    """Return a short alphanumeric id suitable for Converse ``toolUseId`` fields."""
    return secrets.token_hex(5)


def normalize_tool_input_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a tool's public input schema for ``toolSpec.inputSchema.json``."""
    return normalize_object_tool_input_schema(
        schema,
        unsupported_keys=BEDROCK_UNSUPPORTED_SCHEMA_KEYS,
    )


def build_converse_tool_specs(tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
    """Build ``toolConfig.tools`` entries from registered tool objects."""
    specs: list[dict[str, Any]] = []
    for tool in tools:
        specs.append(
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": {"json": normalize_tool_input_schema(tool.public_input_schema)},
                }
            }
        )
    return specs


def to_converse_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert agent messages to Converse ``messages`` shape."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            converted.append({"role": message["role"], "content": [{"text": content}]})
        else:
            converted.append(message)
    return converted


def build_assistant_tool_use_message(tool_calls: list[Any]) -> dict[str, Any]:
    """Build a Converse assistant message containing ``toolUse`` blocks."""
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                }
            }
            for tc in tool_calls
        ],
    }


def build_tool_result_message(tool_calls: list[Any], results: list[Any]) -> dict[str, Any]:
    """Build the Converse ``toolResult`` user message for one round of tool calls."""
    content: list[dict[str, Any]] = []
    for tc, result in zip(tool_calls, results, strict=True):
        is_error = isinstance(result, dict) and bool(result.get("error"))
        if isinstance(result, dict):
            sanitized = json.loads(json.dumps(result, default=str))
            result_content: list[dict[str, Any]] = [{"json": sanitized}]
        else:
            result_content = [{"text": json.dumps(result, default=str)}]
        tool_result: dict[str, Any] = {
            "toolUseId": tc.id,
            "content": result_content,
        }
        if is_error:
            tool_result["status"] = "error"
        content.append({"toolResult": tool_result})
    return {"role": "user", "content": content}


def parse_converse_output(
    response: dict[str, Any],
) -> tuple[str, list[tuple[str, str, dict[str, Any]]], str, dict[str, Any]]:
    """Parse a Converse API response into text, tool calls, stop reason, and raw message."""
    output_message = response.get("output", {}).get("message", {})
    if not isinstance(output_message, dict):
        output_message = {"role": "assistant", "content": []}

    text_parts: list[str] = []
    tool_calls: list[tuple[str, str, dict[str, Any]]] = []
    for block in output_message.get("content", []):
        if not isinstance(block, dict):
            continue
        if "text" in block:
            text_parts.append(str(block["text"]))
            continue
        tool_use = block.get("toolUse")
        if not isinstance(tool_use, dict):
            continue
        raw_input = tool_use.get("input")
        tool_calls.append(
            (
                str(tool_use["toolUseId"]),
                str(tool_use["name"]),
                raw_input if isinstance(raw_input, dict) else {},
            )
        )

    stop_reason = str(response.get("stopReason", "end_turn"))
    return "".join(text_parts), tool_calls, stop_reason, output_message


def map_bedrock_client_error(model: str, err: Any) -> RuntimeError:
    """Map a ``botocore`` ``ClientError`` to a user-facing ``RuntimeError``."""
    code = err.response.get("Error", {}).get("Code", "")
    message = err.response.get("Error", {}).get("Message", "") or str(err)

    if code == "ValidationException":
        return RuntimeError(f"Bedrock request rejected (HTTP 400): {message}")
    if code == "ResourceNotFoundException":
        return RuntimeError(
            f"Bedrock model '{model}' was not found in the configured region. "
            "Check the model ID, region, or inference profile."
        )
    if code == "ThrottlingException":
        return RuntimeError(
            f"Bedrock rate limit exceeded for model '{model}'. "
            "Reduce request frequency or request a quota increase."
        )
    if code in ("AccessDeniedException", "UnauthorizedException"):
        err_msg_str = str(message)
        if (
            "INVALID_PAYMENT_INSTRUMENT" in err_msg_str
            or "payment instrument" in err_msg_str.lower()
        ):
            aws_message = err_msg_str.strip().rstrip(".")
            detail = f" Cause: {aws_message}." if aws_message else ""
            return RuntimeError(
                f"Access denied for Bedrock model '{model}'.{detail} "
                "A valid AWS payment instrument is required."
            )
        aws_message = err_msg_str.strip().rstrip(".")
        detail = f" Cause: {aws_message}." if aws_message else ""
        return RuntimeError(
            f"Access denied for Bedrock model '{model}'.{detail} "
            "Check Bedrock model access (per-region opt-in), your "
            "AWS Marketplace subscription / payment method, and "
            "IAM permissions."
        )
    return RuntimeError(f"Bedrock API request failed: {message}")


def is_non_retryable_bedrock_code(code: str) -> bool:
    """Return True when retrying the same request will not help."""
    return code in (
        "ValidationException",
        "ResourceNotFoundException",
        "AccessDeniedException",
        "UnauthorizedException",
    )
