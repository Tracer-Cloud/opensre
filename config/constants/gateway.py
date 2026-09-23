"""Gateway runtime constants."""

ATTACHMENT_MAX_FILE_CHARS = 40_000
ATTACHMENT_MAX_TOTAL_CHARS = 120_000
CREDITS_DENIED_MESSAGE = "Out of credits — top up in the OpenSRE console."
#: Overall SIGTERM budget for web + chat workers. Sequential stop steps share it.
DEFAULT_STOP_TIMEOUT_SECONDS = 8.0
#: Overrides that budget; a hosted task sets it just under its ECS ``stopTimeout``.
GATEWAY_STOP_TIMEOUT_SECONDS_ENV = "OPENSRE_GATEWAY_STOP_TIMEOUT_SECONDS"
#: Ceiling for the override: the longest ``stopTimeout`` Fargate allows.
MAX_STOP_TIMEOUT_SECONDS = 120.0
#: How long a health check waits for the scheduler task-store lock before counting zero.
HEALTH_TASK_STORE_LOCK_TIMEOUT_SECONDS = 1.0
#: Share of the remaining budget that running scheduled jobs may use to finish.
SCHEDULER_STOP_BUDGET_SHARE = 0.5
#: Web is a thread join, not a network drain, so it keeps a smaller slice.
WEB_STOP_TIMEOUT_SECONDS = 5.0
#: Reload watcher only polls a flag; cap the join so chat workers keep the rest.
SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS = 2.0
DEFAULT_MAX_CONVERSATION_LOCKS = 1024
NEW_SESSION_MESSAGE = "Started a new session."
#: Inbound-decision reply sentinel: rotate the session instead of replying.
ROTATE_SESSION = "__ROTATE_SESSION__"
NO_ACTIVE_TURN_MESSAGE = "Nothing running to stop."
TURN_ERROR_MESSAGE = "Something went wrong on that request."
TURN_TIMEOUT_MESSAGE = "This is taking longer than expected. Please try again."
UNAUTHORIZED_MESSAGE = "You're not authorized to use this bot. Ask an admin to add you."
USER_STOP_MESSAGE = "Stopped."

#: Remote prompt intake: one prompt in, one answer out, polled by id.
PROMPT_ROUTE_PATH = "/v1/prompt"
PROMPT_MAX_CHARS = 8_000
PROMPT_CONTEXT_MAX_ITEMS = 16
PROMPT_CONTEXT_VALUE_MAX_CHARS = 512
PROMPT_QUEUE_MAX = 8
PROMPT_RESULT_RETENTION_SECONDS = 3_600.0
#: Actor recorded for a remote prompt when the caller names none.
PROMPT_DEFAULT_ACTOR = "remote-shell"
#: Progress lines a prompt record keeps (the newest), and the length each is cut to.
PROMPT_PROGRESS_MAX_LINES = 20
PROMPT_PROGRESS_LINE_MAX_CHARS = 200
#: The prompt worker ends after its current job; it gets this slice of the stop budget.
PROMPT_WORKER_STOP_TIMEOUT_SECONDS = 2.0

#: Postgres DSN for the gateway's shared repositories; unset means process-local storage.
DATABASE_URL_ENV = "DATABASE_URL"

__all__ = [
    "DATABASE_URL_ENV",
    "ATTACHMENT_MAX_FILE_CHARS",
    "ATTACHMENT_MAX_TOTAL_CHARS",
    "CREDITS_DENIED_MESSAGE",
    "DEFAULT_MAX_CONVERSATION_LOCKS",
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "GATEWAY_STOP_TIMEOUT_SECONDS_ENV",
    "HEALTH_TASK_STORE_LOCK_TIMEOUT_SECONDS",
    "MAX_STOP_TIMEOUT_SECONDS",
    "NEW_SESSION_MESSAGE",
    "ROTATE_SESSION",
    "SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS",
    "SCHEDULER_STOP_BUDGET_SHARE",
    "NO_ACTIVE_TURN_MESSAGE",
    "PROMPT_CONTEXT_MAX_ITEMS",
    "PROMPT_CONTEXT_VALUE_MAX_CHARS",
    "PROMPT_DEFAULT_ACTOR",
    "PROMPT_MAX_CHARS",
    "PROMPT_PROGRESS_LINE_MAX_CHARS",
    "PROMPT_PROGRESS_MAX_LINES",
    "PROMPT_QUEUE_MAX",
    "PROMPT_RESULT_RETENTION_SECONDS",
    "PROMPT_ROUTE_PATH",
    "PROMPT_WORKER_STOP_TIMEOUT_SECONDS",
    "TURN_ERROR_MESSAGE",
    "TURN_TIMEOUT_MESSAGE",
    "UNAUTHORIZED_MESSAGE",
    "USER_STOP_MESSAGE",
    "WEB_STOP_TIMEOUT_SECONDS",
]
