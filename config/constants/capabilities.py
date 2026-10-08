"""Capability names a host records on a session for tools to read."""

#: Set by a long-lived host whose in-process scheduler picks up tasks added to the
#: store, so a scheduling tool need not install an OS-level service.
SCHEDULER_HOST_CAPABILITY = "scheduler_host"
SCHEDULER_HOST_IN_PROCESS = "in_process"
#: Tools that call the OpenSRE app with this machine's account token. An empty
#: value means the host has no such sign-in, so those tools stay off the turn.
HOSTED_GATEWAY_CAPABILITY = "hosted_gateway"
#: Mutation-capable calls through the generic MCP integration. Gateway chat
#: hosts omit this while local interactive sessions grant the mutation tool.
MCP_GATEWAY_MUTATION_CAPABILITY = "mcp_gateway_mutation"
MCP_GATEWAY_MUTATION_TOOL = "call_mcp_gateway_tool"

__all__ = [
    "HOSTED_GATEWAY_CAPABILITY",
    "MCP_GATEWAY_MUTATION_CAPABILITY",
    "MCP_GATEWAY_MUTATION_TOOL",
    "SCHEDULER_HOST_CAPABILITY",
    "SCHEDULER_HOST_IN_PROCESS",
]
