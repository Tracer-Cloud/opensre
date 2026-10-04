"""Tool utilities — the one public API consumers use to import these helpers.

Schema builders, MCP payload readers, code-host and availability envelopes,
database-warning wrappers, and the registry tools prefetch slow reads with.
Importing the submodules directly still works inside this package; everything
above the tool tier comes through here so the split below can change without a
sweep across ``integrations/`` and ``tools/``.
"""

from core.tool_framework.utils.code_host_unavailable import code_host_unavailable_payload
from core.tool_framework.utils.data_validation import validate_host_metrics
from core.tool_framework.utils.db_warnings import default_db_warning
from core.tool_framework.utils.mcp_bridge import unavailable_response
from core.tool_framework.utils.mcp_params import first_list, first_string
from core.tool_framework.utils.mcp_tool_listing import build_mcp_tool_listing
from core.tool_framework.utils.prefetch import PrefetchRegistry
from core.tool_framework.utils.schema import (
    object_schema,
    string_array_property,
    string_property,
)
from core.tool_framework.utils.sql_wrapper import call_db_tool_with_default_db_warning
from core.tool_framework.utils.tool_availability import (
    envelope_setup_command,
    envelope_source_id,
    is_tool_unavailable_envelope,
    tool_unavailable,
)

__all__ = [
    "PrefetchRegistry",
    "build_mcp_tool_listing",
    "call_db_tool_with_default_db_warning",
    "code_host_unavailable_payload",
    "default_db_warning",
    "envelope_setup_command",
    "envelope_source_id",
    "first_list",
    "first_string",
    "is_tool_unavailable_envelope",
    "object_schema",
    "string_array_property",
    "string_property",
    "tool_unavailable",
    "unavailable_response",
    "validate_host_metrics",
]
