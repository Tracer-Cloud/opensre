"""Gitee MCP integration verifier."""

from __future__ import annotations

from integrations.gitee import build_gitee_mcp_config, validate_gitee_mcp_config
from integrations.verification import register_validation_verifier

verify_gitee_mcp = register_validation_verifier(
    "gitee_mcp",
    build_config=build_gitee_mcp_config,
    validate_config=validate_gitee_mcp_config,
)
