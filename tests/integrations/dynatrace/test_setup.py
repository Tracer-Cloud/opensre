"""Dynatrace setup specification tests."""

from __future__ import annotations

from config.constants.dynatrace import DYNATRACE_PLATFORM_TOKEN_ENV, DYNATRACE_TENANT_URL_ENV
from integrations.dynatrace.setup import (
    DYNATRACE_SETUP,
    PLATFORM_TOKEN_FIELD,
    TENANT_URL_FIELD,
)
from integrations.dynatrace.verifier import verify_dynatrace
from tools.registry import get_registered_tool, integration_of_tool
from tools.registry_discovery import INTEGRATION_TOOL_PACKAGES


def test_setup_spec_and_tool_discovery_are_wired() -> None:
    assert DYNATRACE_SETUP.service == "dynatrace"
    assert DYNATRACE_SETUP.verify is verify_dynatrace
    fields = {field.name: field for field in DYNATRACE_SETUP.fields}
    assert fields[TENANT_URL_FIELD].env_var == DYNATRACE_TENANT_URL_ENV
    assert fields[TENANT_URL_FIELD].secret is False
    assert fields[PLATFORM_TOKEN_FIELD].env_var == DYNATRACE_PLATFORM_TOKEN_ENV
    assert fields[PLATFORM_TOKEN_FIELD].secret is True
    assert "integrations.dynatrace.tools" in INTEGRATION_TOOL_PACKAGES


def test_logs_tool_is_visible_through_the_normal_registry() -> None:
    tool = get_registered_tool("query_dynatrace_logs")
    assert tool is not None
    assert tool.source == "dynatrace"
    assert integration_of_tool("query_dynatrace_logs") == "dynatrace"
