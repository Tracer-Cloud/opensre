"""Sentry SDK initialisation for runtime error monitoring.

Initialises Sentry using the project DSN constant.  Call ``init_sentry()`` once
early in each process entry-point (CLI, ASGI server, etc.).  Repeated calls
are safe — the function is idempotent.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from contextlib import suppress
from functools import cache
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from config.constants import (
    SENTRY_DSN,
    SENTRY_ERROR_SAMPLE_RATE,
    SENTRY_IN_APP_INCLUDE,
    SENTRY_MAX_BREADCRUMBS,
    SENTRY_TRACES_SAMPLE_RATE,
)
from infrastructure.analytics.events import Event

_HOME_PATH_RE: re.Pattern[str] = re.compile(r"/(?:Users|home)/[^/\s]+")
# Pydantic V2 ValidationError messages render ``input_value=<repr>`` (or
# ``input=<repr>``) for each failing field. When the failing field is e.g.
# ``api_token`` or ``password``, the raw secret lands in the rendered text
# and reaches Sentry through ``exception.values[].value`` — a layer the
# existing key-based scrubbers do not walk. Strip the value up to the next
# pydantic field separator, which is always ``, input_type=``. ``\Z`` is the
# end-of-string fallback so a truncated message (no ``, input_type=`` after
# the secret) still gets scrubbed instead of silently leaking. The placeholder
# left behind (``input_value=[Filtered]``) has no ``, input_type=`` immediately
# after the bracket, so re-applying the scrub is a no-op (idempotent).
_PYDANTIC_INPUT_RE: re.Pattern[str] = re.compile(
    r"input(?:_value)?=.*?(?=,\s*input_type=|\Z)",
    flags=re.DOTALL,
)
_SENSITIVE_KEY_SUFFIXES: tuple[str, ...] = ("_token", "_key", "_secret", "_password")
_SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "prompt",
    "messages",
    "dsn",
    "bearer",
    "cookie",
    "auth",
    "credential",
)
_SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {"authorization", "cookie", "set-cookie", "x-api-key"}
)
_QUERY_SCRUBBING_CATEGORIES: frozenset[str] = frozenset({"http", "httpx"})
_HEADER_SCRUBBING_CATEGORIES: frozenset[str] = frozenset({"http", "httpx", "aiohttp"})
_HOSTED_ENTRYPOINTS: frozenset[str] = frozenset({"webapp", "remote", "mcp", "pipeline", "gateway"})
_OPERATOR_ACTIONABLE_LLM_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Any provider auth failure: "Openrouter authentication failed. Check OPENROUTER_API_KEY …"
    re.compile(r"\bauthentication failed\.\s+Check\s+\S+_API_KEY\b", re.I),
    re.compile(r"\bmissing\s+[A-Z0-9_]+_API_KEY\b", re.I),
    # Pydantic validation: "LLM provider 'minimax' requires MINIMAX_API_KEY to be set."
    re.compile(r"\brequires\s+[A-Z0-9_]+_API_KEY\s+to\s+be\s+set\b", re.I),
    re.compile(r"\brate limit exceeded\b.*\b(?:quota|billing)\b", re.I),
    re.compile(r"\bcredit balance is too low\b", re.I),
    # llm_clients.py uses "was not found"; agent_clients.py uses "not found" — cover both.
    re.compile(r"\bmodel\s+['\"][^'\"]+['\"]\s+(?:was )?not found\b", re.I),
    re.compile(r"\bcheck your configured model name or endpoint\b", re.I),
    # Relay/proxy forwarding an invalid model group to Anthropic.
    re.compile(r"\bprovided model identifier is invalid\b", re.I),
    re.compile(r"\bLLM API request failed after multiple retries\b", re.I),
    # Provider endpoint unreachable (Ollama down, bad URL, SSL misconfiguration).
    re.compile(r"\bcannot connect to .+ api\b", re.I),
    # Provider read timeout after retries — anchored to the suffix produced by
    # _format_openai_connection_error so generic non-LLM timeout messages are unaffected.
    re.compile(r"\bapi request timed out\. check that the service is running\b", re.I),
    # Anthropic / provider account-level usage-limit enforcement (HTTP 400).
    re.compile(r"\byou have reached your specified api usage limits\b", re.I),
    # Billing quota exhausted: catches the OpenAI ``insufficient_quota`` path
    # (``"<provider> billing quota exceeded. ..."``) and the Bedrock Anthropic
    # ``usage limits`` path (``"Anthropic billing quota exceeded for Bedrock model ..."``).
    re.compile(r"\bbilling quota exceeded\b", re.I),
    # Bedrock account/model access failures are operator-actionable: the AWS
    # account, Marketplace subscription/payment setup, region model access, or
    # IAM policy needs to change before retrying can succeed.
    re.compile(r"\bBedrock model\s+['\"][^'\"]+['\"]\s+is not available for your account\b", re.I),
    # Bedrock cross-region inference profile misconfiguration (HTTP 400 "on-demand throughput
    # isn't supported") — user must add the 'us.' prefix to their model ID.
    re.compile(r"\brequires a cross-region inference profile\b", re.I),
)


class _ScopeTagsState:
    """Mutable holder for the first-wins scope-tag guard.

    Wrapped in a class so the flag is read/written via attribute access on
    a stable container, avoiding the ``global`` keyword (which CodeQL's
    ``py/unused-global-variable`` rule misreports despite the in-function
    reads, see github-advanced-security review on PR #1583).
    """

    applied: bool = False


def _is_sentry_disabled() -> bool:
    return (
        os.getenv("OPENSRE_NO_TELEMETRY", "0") == "1"
        or os.getenv("OPENSRE_SENTRY_DISABLED", "0") == "1"
        or os.getenv("DO_NOT_TRACK", "0") == "1"
    )


def _sample_rate_from_env(env_var: str, default: float) -> float:
    try:
        sample_rate = float(os.getenv(env_var, str(default)))
    except ValueError:
        return default
    return min(1.0, max(0.0, sample_rate))


def _resolved_dsn() -> str:
    """Allow env overrides while keeping the bundled DSN as the default."""
    return os.getenv("OPENSRE_SENTRY_DSN") or os.getenv("SENTRY_DSN") or SENTRY_DSN


def resolved_sentry_dsn_host() -> str:
    """Return the resolved Sentry DSN host without exposing credentials."""
    dsn = _resolved_dsn()
    if not dsn:
        return ""
    return urlsplit(dsn).hostname or ""


def sentry_transport_enabled() -> bool:
    """Return whether Sentry events are expected to be sent."""
    return bool(_resolved_dsn()) and not _is_sentry_disabled()


def _scrub_string(value: object) -> object:
    if isinstance(value, str):
        return _HOME_PATH_RE.sub("~", value)
    return value


def _is_sensitive_key(key: str) -> bool:
    """True when a key likely carries a secret or LLM payload.

    Combines a suffix check (``_token``, ``_key``, ``_secret``, ``_password``)
    with a permissive substring check against curated terms — the substring
    pass is intentionally aggressive (e.g. ``auth`` matches ``oauth_provider``)
    to err on the side of redaction over leakage.
    """
    lowered = key.lower()
    if any(lowered.endswith(suffix) for suffix in _SENSITIVE_KEY_SUFFIXES):
        return True
    return any(substring in lowered for substring in _SENSITIVE_KEY_SUBSTRINGS)


def _scrub_mapping_recursive(mapping: dict[str, Any]) -> None:
    for key, value in list(mapping.items()):
        if _is_sensitive_key(key):
            mapping[key] = "[Filtered]"
            continue
        if isinstance(value, dict):
            _scrub_mapping_recursive(value)
        elif isinstance(value, list):
            _scrub_list_recursive(value)


def _scrub_list_recursive(items: list[Any]) -> None:
    for item in items:
        if isinstance(item, dict):
            _scrub_mapping_recursive(item)
        elif isinstance(item, list):
            _scrub_list_recursive(item)


def _scrub_request(request: dict[str, Any]) -> None:
    headers = request.get("headers")
    if isinstance(headers, dict):
        for header in list(headers):
            if header.lower() in _SENSITIVE_HEADERS:
                headers[header] = "[Filtered]"
    if "cookies" in request:
        request["cookies"] = "[Filtered]"
    for body_key in ("data", "body"):
        body = request.get(body_key)
        if isinstance(body, dict):
            _scrub_mapping_recursive(body)
        elif isinstance(body, list):
            _scrub_list_recursive(body)
        elif isinstance(body, str):
            # FastAPI/Starlette integration captures `request.body` as a raw
            # JSON string; parse it so the recursive scrubber can walk it.
            try:
                parsed = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                _scrub_mapping_recursive(parsed)
                request[body_key] = parsed
            elif isinstance(parsed, list):
                _scrub_list_recursive(parsed)
                request[body_key] = parsed


def _scrub_extra(extra: dict[str, Any]) -> None:
    """Recursively scrub the ``extra`` payload.

    Sentry's ``extra`` field accepts arbitrary mappings, and ``capture_exception``
    callers frequently pass nested dicts (e.g. an LLM context block). Walking
    only the top level would let nested secrets and prompts through.
    """
    _scrub_mapping_recursive(extra)


def _scrub_stacktrace_frames(frames: list[dict[str, Any]]) -> None:
    for frame in frames:
        for path_key in ("abs_path", "filename"):
            if path_key in frame:
                frame[path_key] = _scrub_string(frame[path_key])
        local_vars = frame.get("vars")
        if isinstance(local_vars, dict):
            for key, value in list(local_vars.items()):
                if _is_sensitive_key(key):
                    local_vars[key] = "[Filtered]"
                else:
                    local_vars[key] = _scrub_string(value)


def _scrub_exception_value(text: str) -> str:
    """Strip rendered field values from an exception message string.

    Pydantic V2 ``ValidationError`` is the main offender — the renderer
    embeds ``input_value=<repr>`` for each failing field, which leaks the
    raw value (often a secret) into ``exception.values[].value`` where the
    existing key-based scrubbers do not reach. ``_HOME_PATH_RE`` is also
    applied so home-directory paths in arbitrary messages get the same
    treatment they do elsewhere.
    """
    scrubbed = _PYDANTIC_INPUT_RE.sub("input_value=[Filtered]", text)
    return _HOME_PATH_RE.sub("~", scrubbed)


def _scrub_event_in_place(event: dict[str, Any]) -> None:
    request = event.get("request")
    if isinstance(request, dict):
        _scrub_request(request)

    extra = event.get("extra")
    if isinstance(extra, dict):
        _scrub_extra(extra)

    exception = event.get("exception")
    if isinstance(exception, dict):
        for entry in exception.get("values", []) or []:
            if not isinstance(entry, dict):
                continue
            value = entry.get("value")
            if isinstance(value, str):
                entry["value"] = _scrub_exception_value(value)
            stacktrace = entry.get("stacktrace")
            if isinstance(stacktrace, dict):
                frames = stacktrace.get("frames")
                if isinstance(frames, list):
                    _scrub_stacktrace_frames(frames)


def _event_has_operator_actionable_llm_error(event: dict[str, Any]) -> bool:
    """Return True for provider/account failures that users can fix outside OpenSRE.

    These errors are still rendered to the CLI user, but they should not create
    high-priority Sentry issues because they usually mean a bad key, exhausted
    quota, missing local model, or temporary provider connectivity.
    """
    exception = event.get("exception")
    if not isinstance(exception, dict):
        return False

    values = exception.get("values")
    if not isinstance(values, list):
        return False

    combined_parts: list[str] = []
    for entry in values:
        if not isinstance(entry, dict):
            continue
        exc_type = entry.get("type")
        exc_value = entry.get("value")
        if isinstance(exc_type, str):
            combined_parts.append(exc_type)
        if isinstance(exc_value, str):
            combined_parts.append(exc_value)

    combined = "\n".join(combined_parts)
    return any(pattern.search(combined) for pattern in _OPERATOR_ACTIONABLE_LLM_ERROR_PATTERNS)


def _apply_fingerprint_rules(event: dict[str, Any]) -> None:
    tags = event.get("tags")
    if not isinstance(tags, dict):
        return

    tool_name = tags.get("tool")
    if isinstance(tool_name, str) and tool_name:
        event["fingerprint"] = ["tool-error", tool_name, "{{ default }}"]
        return

    node_name = tags.get("node")
    if isinstance(node_name, str) and node_name:
        event["fingerprint"] = ["node-error", node_name, "{{ default }}"]


def _before_send(event: Any, _hint: dict[str, Any]) -> Any:
    """Drop or scrub a Sentry event before transport.

    Returns ``None`` to drop the event (e.g. when DSN is empty or telemetry
    is disabled), otherwise returns the same dict with sensitive bits
    replaced with ``[Filtered]``.
    """
    if _is_sentry_disabled():
        return None
    if not _resolved_dsn():
        return None
    if not isinstance(event, dict):
        return event
    if _event_has_operator_actionable_llm_error(event):
        return None
    _apply_fingerprint_rules(event)
    try:
        _scrub_event_in_place(event)
    except Exception:
        # The hook must never raise — Sentry will swallow the event silently.
        return event
    return event


def _strip_url_query(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


def _scrub_breadcrumb_headers(headers: dict[str, Any]) -> None:
    for header in list(headers):
        if header.lower() in _SENSITIVE_HEADERS:
            headers[header] = "[Filtered]"


def _before_breadcrumb(crumb: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    """Strip query strings and sensitive headers from HTTP breadcrumbs."""
    category = crumb.get("category")
    if not isinstance(category, str):
        return crumb
    data = crumb.get("data")
    if not isinstance(data, dict):
        return crumb
    if category in _QUERY_SCRUBBING_CATEGORIES:
        url = data.get("url")
        if isinstance(url, str):
            data["url"] = _strip_url_query(url)
    if category in _HEADER_SCRUBBING_CATEGORIES:
        headers = data.get("headers")
        if isinstance(headers, dict):
            _scrub_breadcrumb_headers(headers)
    return crumb


def _capture_sentry_init_skipped(reason: str, *, error_type: str | None = None) -> None:
    # Local import to avoid an import cycle between Sentry and analytics modules.
    from infrastructure.analytics.provider import Properties, get_analytics

    properties: Properties = {"reason": reason}
    if error_type is not None:
        properties["error_type"] = error_type
    with suppress(Exception):
        get_analytics().capture(Event.SENTRY_INIT_SKIPPED, properties)


def _build_sentry_integrations() -> list[Any]:
    """Build the Sentry integrations list lazily.

    Importing ``sentry_sdk.integrations.*`` is deferred to the first init so
    that ``config.constants.sentry`` does not pull in ``sentry_sdk`` at import
    time. The CLI bootstrap relies on a ``try: init_sentry() except
    ModuleNotFoundError`` guard to keep ``opensre update`` working when the
    SDK is missing — that guard only fires if the import happens inside
    ``init_sentry``, not at top-level module load.

    Set ``OPENSRE_SENTRY_LOGGING_DISABLED=1`` to disable the
    ``LoggingIntegration`` without affecting ``capture_exception``.
    """
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.httpx import HttpxIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    integrations: list[Any] = []

    if os.getenv("OPENSRE_SENTRY_LOGGING_DISABLED", "0") != "1":
        integrations.append(LoggingIntegration(level=logging.INFO, event_level=logging.ERROR))

    integrations.append(AsyncioIntegration())
    integrations.append(HttpxIntegration())
    return integrations


@cache
def _init_sentry_once(
    dsn: str,
    environment: str,
    release: str,
    sample_rate: float,
    traces_sample_rate: float,
) -> None:
    """Initialize Sentry once per effective runtime configuration.

    ``entrypoint`` is intentionally NOT part of the cache key — otherwise a
    webapp process that internally invokes a pipeline runner would call
    ``sentry_sdk.init()`` a second time, re-registering integrations and
    replacing the client. Per-entrypoint differentiation is handled via
    scope tags in :func:`_apply_scope_tags`, which is first-wins.
    """
    import sentry_sdk

    from integrations.llm_cli import (
        CLIAuthenticationRequired,
        CLIInterruptedError,
        CLITimeoutError,
        CLITransientError,
    )

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        send_default_pii=False,
        attach_stacktrace=True,
        sample_rate=sample_rate,
        traces_sample_rate=traces_sample_rate,
        max_breadcrumbs=SENTRY_MAX_BREADCRUMBS,
        in_app_include=list(SENTRY_IN_APP_INCLUDE),
        integrations=_build_sentry_integrations(),
        auto_enabling_integrations=False,
        before_send=_before_send,
        before_breadcrumb=_before_breadcrumb,
        ignore_errors=[
            KeyboardInterrupt,
            CLIAuthenticationRequired,
            CLIInterruptedError,
            CLITimeoutError,
            CLITransientError,
        ],
    )


def _apply_scope_tags(entrypoint: str | None) -> None:
    """Apply runtime scope tags after init — first-wins.

    Called once per process; subsequent ``init_sentry()`` calls (e.g. when a
    pipeline runner is invoked from inside a webapp) are no-ops at this
    layer so the outermost entrypoint dictates the tags. Wrapped at the
    call site in ``suppress(Exception)`` because the tagging must never
    break the init flow if the SDK is stubbed (e.g. in tests).

    The runtime tag is namespaced as ``opensre.runtime`` to avoid colliding
    with Sentry's built-in ``runtime`` context (which carries the Python
    runtime, e.g. ``CPython 3.12``, and is flattened into a tag of the same
    name by Sentry's event processor — overriding any plain ``runtime`` tag
    set on the scope). Server-side surfaces (``webapp``/``remote``/``mcp``/
    ``pipeline``/``gateway``) map to ``hosted``; everything else maps to
    ``cli`` — this matches the surface, not the ``ENV`` setting, so a
    webapp running locally still reports as ``hosted``.
    """
    if _ScopeTagsState.applied:
        return
    runtime = "hosted" if entrypoint in _HOSTED_ENTRYPOINTS else "cli"
    deployment_method = os.getenv("OPENSRE_DEPLOYMENT_METHOD", "local")
    import sentry_sdk

    sentry_sdk.set_tag("entrypoint", entrypoint or "unknown")
    sentry_sdk.set_tag("opensre.runtime", runtime)
    sentry_sdk.set_tag("deployment_method", deployment_method)
    _ScopeTagsState.applied = True


def _reset_scope_tags_state_for_tests() -> None:
    """Reset the first-wins guard. Test-only helper."""
    _ScopeTagsState.applied = False


def init_sentry(entrypoint: str | None = None) -> None:
    """Configure and start the Sentry SDK if a DSN is available.

    DSN sourcing precedence: ``OPENSRE_SENTRY_DSN`` env var, ``SENTRY_DSN``
    env var, then the bundled constant. Set ``OPENSRE_NO_TELEMETRY=1`` or
    ``DO_NOT_TRACK=1`` to disable both Sentry and product analytics.
    ``OPENSRE_SENTRY_DISABLED=1`` disables Sentry only;
    ``OPENSRE_SENTRY_LOGGING_DISABLED=1`` disables automatic forwarding of
    ``logger.error`` and ``logger.exception`` calls to Sentry as events,
    without affecting ``capture_exception``.
    ``OPENSRE_ANALYTICS_DISABLED=1`` disables product analytics only.

    ``entrypoint`` identifies the calling surface (``cli``, ``webapp``,
    ``remote``, ``mcp``, ``integrations``, ``wizard``, ``pipeline``,
    ``gateway``) and is attached as a scope tag for grouping in Sentry.
    The first non-no-op call wins — inner callers cannot overwrite the
    outer entrypoint's tags.
    """
    if _is_sentry_disabled():
        _capture_sentry_init_skipped("telemetry_disabled")
        return

    from config.environment import get_environment
    from config.version import get_opensre_version

    try:
        _init_sentry_once(
            dsn=_resolved_dsn(),
            environment=get_environment().value,
            release=f"opensre@{get_opensre_version()}",
            sample_rate=_sample_rate_from_env(
                "SENTRY_ERROR_SAMPLE_RATE",
                SENTRY_ERROR_SAMPLE_RATE,
            ),
            traces_sample_rate=_sample_rate_from_env(
                "SENTRY_TRACES_SAMPLE_RATE",
                SENTRY_TRACES_SAMPLE_RATE,
            ),
        )
    except ModuleNotFoundError:
        _capture_sentry_init_skipped("missing_sdk", error_type="ModuleNotFoundError")
        raise
    except Exception as exc:
        _capture_sentry_init_skipped("init_error", error_type=type(exc).__name__)
        raise

    if not _resolved_dsn():
        return
    with suppress(Exception):
        _apply_scope_tags(entrypoint)


def capture_exception(
    exc: BaseException,
    *,
    context: str | None = None,
    extra: Mapping[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
) -> str | None:
    """Best-effort capture for exceptions swallowed by boundary adapters."""
    if _is_sentry_disabled():
        return None
    with suppress(Exception):
        import sentry_sdk

        if context is None and not extra and not tags:
            return cast("str | None", sentry_sdk.capture_exception(exc))
        with sentry_sdk.push_scope() as scope:
            if context is not None:
                scope.set_tag("opensre.context", context)
            if tags:
                for key, value in tags.items():
                    scope.set_tag(key, value)
            if extra:
                for key, value in extra.items():
                    scope.set_extra(key, value)
            return cast("str | None", sentry_sdk.capture_exception(exc))
    return None
