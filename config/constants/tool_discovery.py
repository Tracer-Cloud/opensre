"""Progressive tool-catalog policy shared by the harness and ReAct loop."""

INITIAL_TOOL_CATALOG_LIMIT = 20
INITIAL_TOOL_SCHEMA_TOKEN_LIMIT = 6_000
TOOL_SEARCH_MAX_RESULTS = 8
MAX_STAGNANT_DISCOVERY_ITERATIONS = 3

TOOL_SEARCH_NAME = "tool_search"
MODEL_ONLY_PRESENTATION_KEY = "model_only"
DISCOVERY_PROGRESS_KEY = "discovery_progress"
DISCOVERY_DETAIL_KEYS_KEY = "discovery_detail_keys"
TOOL_FAILURE_STATE_KEY = "failure_state"

TOOL_DISCOVERY_STATE_MATCHED = "matched"
TOOL_DISCOVERY_STATE_NO_MATCH = "no_match"
TOOL_DISCOVERY_STATE_MISSING_CONFIG = "missing_config"
TOOL_DISCOVERY_STATE_DENIED = "denied"
TOOL_DISCOVERY_STATE_TRANSPORT_FAILURE = "transport_failure"
TOOL_DISCOVERY_STATE_MISSING_DATA = "missing_data"
TOOL_FAILURE_STATES = frozenset(
    {
        TOOL_DISCOVERY_STATE_NO_MATCH,
        TOOL_DISCOVERY_STATE_MISSING_CONFIG,
        TOOL_DISCOVERY_STATE_DENIED,
        TOOL_DISCOVERY_STATE_TRANSPORT_FAILURE,
        TOOL_DISCOVERY_STATE_MISSING_DATA,
    }
)

# Order is deliberate. Session-goal controls and active workflow helpers are
# inserted before the trailing entries and displace them at the hard cap.
INITIAL_TOOL_CATALOG_ORDER = (
    TOOL_SEARCH_NAME,
    "skill_view",
    "ask_user_choice",
    "update_plan",
    "cli_exec",
    "slash_invoke",
    "scan_local_git_workspace",
    "analyze_local_repositories",
    "analyze_github_ci_reliability",
    "scan_github_ci_health",
    "github_cli",
    "fix_github_pr_ci",
    "run_ci_repair_demo",
    "schedule_ci_repair_loop",
    "get_ci_repair_loop",
    "check_hosted_gateway",
    "start_hosted_gateway",
    "ask_hosted_gateway",
    "probe_github_repair_access",
    "finish_ci_repair_demo",
)

SESSION_GOAL_CONTROL_TOOL_NAMES = (
    "session_goal_complete",
    "task_cancel",
)

__all__ = [
    "DISCOVERY_PROGRESS_KEY",
    "DISCOVERY_DETAIL_KEYS_KEY",
    "INITIAL_TOOL_CATALOG_LIMIT",
    "INITIAL_TOOL_CATALOG_ORDER",
    "INITIAL_TOOL_SCHEMA_TOKEN_LIMIT",
    "MAX_STAGNANT_DISCOVERY_ITERATIONS",
    "MODEL_ONLY_PRESENTATION_KEY",
    "SESSION_GOAL_CONTROL_TOOL_NAMES",
    "TOOL_DISCOVERY_STATE_DENIED",
    "TOOL_DISCOVERY_STATE_MATCHED",
    "TOOL_DISCOVERY_STATE_MISSING_CONFIG",
    "TOOL_DISCOVERY_STATE_MISSING_DATA",
    "TOOL_DISCOVERY_STATE_NO_MATCH",
    "TOOL_DISCOVERY_STATE_TRANSPORT_FAILURE",
    "TOOL_FAILURE_STATE_KEY",
    "TOOL_FAILURE_STATES",
    "TOOL_SEARCH_MAX_RESULTS",
    "TOOL_SEARCH_NAME",
]
