"""Analytics transport for the OpenSRE CLI."""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import math
import os
import platform
import queue
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx

from config.constants import get_store_path
from config.constants.analytics import (
    ANALYTICS_DISABLED_ENV,
    ANALYTICS_EVENT_SCHEMA_VERSION,
    ANALYTICS_LOG_EVENTS_ENV,
    ANALYTICS_MAX_PAYLOAD_BYTES,
    ANALYTICS_SOURCE,
)
from config.version import get_opensre_version
from infrastructure.analytics.analytics_runtime import (
    detect_analytics_runtime,
    detect_container_runtime,
    is_ci_environment,
)
from infrastructure.analytics.destination import (
    AnalyticsDestination,
    resolve_analytics_destination,
)
from infrastructure.analytics.events import Event
from infrastructure.analytics.usage_context import (
    ORGANIZATION_GROUP_TYPE,
    merge_usage_enrichment,
)

_CONFIG_DIR = get_store_path().parent
_ANONYMOUS_ID_PATH = _CONFIG_DIR / "anonymous_id"
_FIRST_RUN_PATH = _CONFIG_DIR / "installed"

_QUEUE_SIZE = 128
_SEND_TIMEOUT = 2.0
# Bound how long an explicit flush=True shutdown may block the process.
# Interactive /quit drains under a spinner with this budget; atexit stays non-blocking.
_SHUTDOWN_WAIT = 0.5

_EVENT_LOG_FILENAME: Final[str] = "analytics_events.txt"
_EVENT_LOG_MAX_LINES: Final[int] = 1000
_ANONYMOUS_ID_LOCK_WAIT_SECONDS: Final[float] = 5.0
_ANONYMOUS_ID_LOCK_RETRY_SECONDS: Final[float] = 0.01

_FAILURE_LOG_FILENAME: Final[str] = "analytics_errors.log"
_FAILURE_LOG_MAX_BYTES: Final[int] = 64 * 1024
_FALLBACK_FAILURE_LOG_PATH: Path = Path(tempfile.gettempdir()) / _FAILURE_LOG_FILENAME
_HOME_PATH_RE: Final[re.Pattern[str]] = re.compile(r"/(?:Users|home)/[^/\s]+")
_FAILURE_MESSAGE_MAX_LEN: Final[int] = 240
_COMPOSITE_FINGERPRINT_VERSION: Final[str] = "hashed-local-v1"
_COMPOSITE_FINGERPRINT_NAMESPACE: Final[str] = "opensre-cli-analytics-fingerprint"
_CI_FINGERPRINT_ENV_KEYS: Final[tuple[str, ...]] = (
    "GITHUB_REPOSITORY",
    "GITHUB_RUNNER_NAME",
    "GITHUB_WORKFLOW",
    "GITLAB_PROJECT_PATH",
    "CI_PROJECT_PATH",
    "CIRCLE_PROJECT_USERNAME",
    "CIRCLE_PROJECT_REPONAME",
    "BUILDKITE_ORGANIZATION_SLUG",
    "BUILDKITE_PIPELINE_SLUG",
    "JENKINS_URL",
    "JOB_NAME",
)

type JsonScalar = str | bool | int | float
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type PropertyValue = JsonValue
type Properties = dict[str, JsonValue]


def _new_event_id() -> str:
    return str(uuid.uuid4())


def _event_timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class _Envelope:
    event: str
    properties: Properties
    destination: AnalyticsDestination | None = field(repr=False)
    event_id: str = field(default_factory=_new_event_id)
    occurred_at: str = field(default_factory=_event_timestamp)


@dataclass(frozen=True, slots=True)
class _AnonymousIdentity:
    distinct_id: str
    persistence: str


@dataclass(frozen=True, slots=True)
class _CompositeFingerprint:
    value: str
    components: str


_anonymous_id_lock = threading.Lock()
_cached_anonymous_id: str | None = None
_cached_identity_persistence = "unknown"
_first_run_marker_created_this_process = False
_pending_user_id_load_failures: list[Properties] = []
_ONE_TIME_EVENTS: Final[frozenset[str]] = frozenset({Event.INSTALL_DETECTED.value})


def _is_opted_out() -> bool:
    return (
        os.getenv("OPENSRE_NO_TELEMETRY", "0") == "1"
        or os.getenv(ANALYTICS_DISABLED_ENV, "0") == "1"
        or os.getenv("DO_NOT_TRACK", "0") == "1"
    )


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _is_existing_install(
    *,
    config_dir_existed: bool,
    install_marker_existed: bool,
) -> bool:
    return (
        config_dir_existed and install_marker_existed
    ) and not _first_run_marker_created_this_process


def _queue_user_id_load_failure(
    reason: str,
    *,
    config_dir_existed: bool,
    install_marker_existed: bool,
    anonymous_id_path_existed: bool,
) -> None:
    if not _is_existing_install(
        config_dir_existed=config_dir_existed,
        install_marker_existed=install_marker_existed,
    ):
        return
    _pending_user_id_load_failures.append(
        {
            "reason": reason,
            "config_dir": "~/.opensre",
            "anonymous_id_path": "~/.opensre/anonymous_id",
            "config_dir_existed": config_dir_existed,
            "install_marker_existed": install_marker_existed,
            "anonymous_id_path_existed": anonymous_id_path_existed,
            "anonymous_id_loaded": False,
        }
    )


def _pop_user_id_load_failures() -> list[Properties]:
    failures = list(_pending_user_id_load_failures)
    _pending_user_id_load_failures.clear()
    return failures


def _valid_anonymous_id(value: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return str(uuid.UUID(stripped))
    except ValueError:
        return None


def _read_persisted_anonymous_id(path: Path) -> str | None:
    return _valid_anonymous_id(path.read_text(encoding="utf-8"))


def _fsync_parent_dir(path: Path) -> None:
    """Best-effort directory fsync so atomic renames survive process crashes on Unix."""
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _write_text_atomic(path: Path, text: str) -> None:
    """Atomically replace ``path`` with ``text`` using a unique sibling temp file."""
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        _fsync_parent_dir(path)
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()


@contextlib.contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    deadline = time.monotonic() + _ANONYMOUS_ID_LOCK_WAIT_SECONDS
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for {lock_path}") from None
            time.sleep(_ANONYMOUS_ID_LOCK_RETRY_SECONDS)
        else:
            try:
                os.write(fd, f"{os.getpid()}\n".encode())
                os.fsync(fd)
            except OSError:
                with contextlib.suppress(OSError):
                    lock_path.unlink()
                raise
            finally:
                os.close(fd)
            break

    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()


def _try_read_persisted_anonymous_id() -> str | None:
    """Read a valid on-disk anonymous id, or None if missing/unreadable/invalid."""
    try:
        if not _ANONYMOUS_ID_PATH.exists():
            return None
        return _read_persisted_anonymous_id(_ANONYMOUS_ID_PATH)
    except OSError:
        return None


def _write_new_anonymous_id(
    new_id: str,
    *,
    replace_existing_invalid: bool = False,
) -> _AnonymousIdentity:
    lock_path = _ANONYMOUS_ID_PATH.with_name(f"{_ANONYMOUS_ID_PATH.name}.lock")
    try:
        with _file_lock(lock_path):
            if _ANONYMOUS_ID_PATH.exists():
                existing = _read_persisted_anonymous_id(_ANONYMOUS_ID_PATH)
                if existing is not None:
                    return _AnonymousIdentity(existing, "disk")
                if not replace_existing_invalid:
                    return _AnonymousIdentity(new_id, "none")
            _write_text_atomic(_ANONYMOUS_ID_PATH, new_id)
        return _AnonymousIdentity(new_id, "disk")
    except OSError:
        # Lock timeout (TimeoutError ⊂ OSError) or I/O failure: prefer a winner's
        # on-disk id over minting a unique ephemeral one per waiter.
        existing = _try_read_persisted_anonymous_id()
        if existing is not None:
            return _AnonymousIdentity(existing, "disk")
        return _AnonymousIdentity(new_id, "none")


def _compute_anonymous_identity() -> _AnonymousIdentity:
    config_dir_existed = _path_exists(_CONFIG_DIR)
    install_marker_existed = _path_exists(_FIRST_RUN_PATH)
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        anonymous_id_path_existed = _ANONYMOUS_ID_PATH.exists()
        if anonymous_id_path_existed:
            existing = _read_persisted_anonymous_id(_ANONYMOUS_ID_PATH)
            if existing is not None:
                return _AnonymousIdentity(existing, "disk")
            _queue_user_id_load_failure(
                "invalid_anonymous_id",
                config_dir_existed=config_dir_existed,
                install_marker_existed=install_marker_existed,
                anonymous_id_path_existed=anonymous_id_path_existed,
            )
        if _is_existing_install(
            config_dir_existed=config_dir_existed,
            install_marker_existed=install_marker_existed,
        ):
            _queue_user_id_load_failure(
                "missing_anonymous_id",
                config_dir_existed=config_dir_existed,
                install_marker_existed=install_marker_existed,
                anonymous_id_path_existed=anonymous_id_path_existed,
            )
        new_id = str(uuid.uuid4())
        return _write_new_anonymous_id(
            new_id,
            replace_existing_invalid=anonymous_id_path_existed,
        )
    except OSError:
        _queue_user_id_load_failure(
            "read_or_write_error",
            config_dir_existed=config_dir_existed,
            install_marker_existed=install_marker_existed,
            anonymous_id_path_existed=_path_exists(_ANONYMOUS_ID_PATH),
        )
        return _AnonymousIdentity(str(uuid.uuid4()), "none")


def _get_or_create_anonymous_id() -> str:
    global _cached_anonymous_id, _cached_identity_persistence
    if _cached_anonymous_id is not None:
        return _cached_anonymous_id
    with _anonymous_id_lock:
        if _cached_anonymous_id is None:
            identity = _compute_anonymous_identity()
            _cached_anonymous_id = identity.distinct_id
            _cached_identity_persistence = identity.persistence
        return _cached_anonymous_id


def _identity_persistence() -> str:
    return _cached_identity_persistence


def _event_insert_id(event: str, distinct_id: str) -> str | None:
    if event not in _ONE_TIME_EVENTS:
        return None
    return f"{event}:{distinct_id}"


def _touch_once(path: Path) -> bool:
    global _first_run_marker_created_this_process
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        _fsync_parent_dir(path)
        if path == _FIRST_RUN_PATH:
            _first_run_marker_created_this_process = True
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def _cli_version() -> str:
    return get_opensre_version()


def _normalized_fingerprint_value(value: object) -> str | None:
    normalized = str(value).strip().casefold()
    return normalized or None


def _add_fingerprint_component(
    components: dict[str, str],
    component_sources: set[str],
    key: str,
    value: object,
    source: str,
) -> None:
    if normalized := _normalized_fingerprint_value(value):
        components[key] = normalized
        component_sources.add(source)


def _env_first(*keys: str) -> str | None:
    for key in keys:
        if value := _normalized_fingerprint_value(os.getenv(key, "")):
            return value
    return None


def _build_composite_fingerprint() -> _CompositeFingerprint:
    components: dict[str, str] = {}
    component_sources: set[str] = set()

    _add_fingerprint_component(
        components, component_sources, "os_family", platform.system(), "platform"
    )
    _add_fingerprint_component(
        components, component_sources, "machine", platform.machine(), "platform"
    )
    _add_fingerprint_component(components, component_sources, "host", platform.node(), "host")
    if user := _env_first("USER", "LOGNAME", "USERNAME"):
        _add_fingerprint_component(components, component_sources, "user", user, "user")
    with contextlib.suppress(RuntimeError, OSError):
        _add_fingerprint_component(
            components, component_sources, "home_name", Path.home().name, "user"
        )
    if is_ci_environment():
        _add_fingerprint_component(components, component_sources, "runtime:ci", "true", "ci")
    if container_runtime := detect_container_runtime():
        _add_fingerprint_component(
            components,
            component_sources,
            "runtime:container",
            container_runtime,
            "container",
        )
    for key in _CI_FINGERPRINT_ENV_KEYS:
        if value := _normalized_fingerprint_value(os.getenv(key, "")):
            components[f"env:{key.casefold()}"] = value
            component_sources.add("ci")

    # Do not emit raw host/user/CI data. Hash sorted key-value pairs so the
    # fingerprint is stable while staying one-way in analytics.
    payload = "\n".join(
        [
            _COMPOSITE_FINGERPRINT_NAMESPACE,
            *(f"{key}={components[key]}" for key in sorted(components)),
        ]
    )
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return _CompositeFingerprint(
        value=fingerprint,
        components=",".join(sorted(component_sources)) or "none",
    )


def _event_logging_enabled() -> bool:
    """Whether the local event log is on. Default is enabled.

    Set ``OPENSRE_ANALYTICS_LOG_EVENTS=0`` to disable. Any value other than
    ``"0"`` (including unset, ``"1"``, ``"true"``, etc.) leaves it on.
    """
    return os.getenv(ANALYTICS_LOG_EVENTS_ENV, "1") != "0"


def _format_property(key: str, value: JsonValue) -> str:
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    else:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
    return f"{key}={rendered}"


@dataclass(slots=True)
class _EventLogState:
    initialized: bool = False
    lines_written: int = 0


_event_log_lock = threading.Lock()
_event_log_state = _EventLogState()


def _event_log_path() -> Path:
    """Resolve the event log path lazily so tests can monkeypatch ``_CONFIG_DIR``.

    The log lives next to ``anonymous_id`` and ``analytics_errors.log`` under
    ``~/.opensre/`` (or the equivalent on other platforms) rather than
    in the user's current working directory. This prevents a stray
    ``analytics_events.txt`` from showing up in every shell where the user runs
    ``opensre``, and keeps related telemetry artifacts in one place.
    """
    return _CONFIG_DIR / _EVENT_LOG_FILENAME


def _initialize_event_log_state(log_path: Path) -> None:
    """Seed the event-log line count from the existing file on disk.

    Called once per process under ``_event_log_lock``. Honoring pre-existing
    content keeps the cap meaningful across runs — without this seed, a user
    whose file already had 1500 lines from a previous process would write a
    further 1000 before the first rotation, growing the file to 2500 lines.
    """
    if _event_log_state.initialized:
        return
    _event_log_state.initialized = True
    with contextlib.suppress(OSError):
        if log_path.exists():
            with log_path.open("r", encoding="utf-8") as fh:
                _event_log_state.lines_written = sum(1 for _ in fh)


def _rotate_event_log(log_path: Path) -> None:
    """Move the live event log aside so the next write starts fresh.

    Uses rename rather than truncate so the most recent ``_EVENT_LOG_MAX_LINES``
    lines are still inspectable in ``<filename>.1`` after rotation.
    """
    backup = log_path.with_name(log_path.name + ".1")
    with contextlib.suppress(OSError):
        if backup.exists():
            backup.unlink()
    with contextlib.suppress(OSError):
        log_path.rename(backup)


def _append_log_line(line: str) -> None:
    """Append one line to the event log, rotating when the line cap is reached.

    Thread-safe: serialized by ``_event_log_lock`` so concurrent callers cannot
    interleave a write with a rename. Failures (e.g. read-only filesystem) are
    swallowed — the event log is a best-effort developer aid, not a guarantee.
    """
    log_path = _event_log_path()
    with _event_log_lock:
        _initialize_event_log_state(log_path)
        # ``_CONFIG_DIR`` may not yet exist on a fresh install, and ``open("a")``
        # on a missing parent dir raises. Suppress because failures here are
        # already non-fatal — see ``_append_log_line`` docstring.
        with contextlib.suppress(OSError):
            log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            return
        _event_log_state.lines_written += 1
        if _event_log_state.lines_written >= _EVENT_LOG_MAX_LINES:
            _rotate_event_log(log_path)
            _event_log_state.lines_written = 0


def _log_event_line(event: str, properties: Properties) -> None:
    if not _event_logging_enabled():
        return
    timestamp = datetime.now(UTC).isoformat()
    parts = [timestamp, event, *(_format_property(k, v) for k, v in properties.items())]
    _append_log_line(" ".join(parts) + "\n")


def _log_debug_line(message: str) -> None:
    """Append a non-event diagnostic line (e.g. send retries before exhaustion).

    Emitted unless ``OPENSRE_ANALYTICS_LOG_EVENTS=0``. Use ``_log_failure``
    instead for terminal failures that must always be recorded.
    """
    if not _event_logging_enabled():
        return
    timestamp = datetime.now(UTC).isoformat()
    _append_log_line(f"{timestamp} {message}\n")


def _scrub_error_message(message: str) -> str:
    """Strip user-identifying paths and cap length for safe persistence."""
    scrubbed = _HOME_PATH_RE.sub("~", message)
    if len(scrubbed) > _FAILURE_MESSAGE_MAX_LEN:
        scrubbed = scrubbed[:_FAILURE_MESSAGE_MAX_LEN] + "..."
    return scrubbed


def _format_failure_extra(value: object) -> JsonValue:
    if isinstance(value, bool):
        return value
    return str(value)


def _failure_breadcrumb_line(stage: str, error: BaseException, extra: dict[str, object]) -> str:
    timestamp = datetime.now(UTC).isoformat()
    parts = [
        timestamp,
        _format_property("stage", stage),
        _format_property("error_type", type(error).__name__),
        _format_property("error_message", _scrub_error_message(str(error))),
    ]
    parts.extend(
        _format_property(key, _format_failure_extra(value)) for key, value in extra.items()
    )
    return " ".join(parts) + "\n"


def _write_failure_line(path: Path, line: str) -> None:
    """Append a failure breadcrumb to ``path`` with naive size-based rotation.

    Rotation uses truncation rather than rename-and-restart because we do not
    care about historical breadcrumbs once the file gets large — the goal is
    diagnostic context for the most recent failures, not an audit log.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > _FAILURE_LOG_MAX_BYTES:
        path.write_text("", encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _log_failure(stage: str, error: BaseException, **extra: object) -> None:
    """Persist a telemetry failure breadcrumb regardless of env configuration.

    Writes a single key=value line to ``<config_dir>/analytics_errors.log`` and
    falls back to ``$TMPDIR/analytics_errors.log`` when the config dir is
    unwritable (the most common cause of init failures). All exceptions are
    swallowed — the telemetry layer must never crash the CLI, even when its
    own diagnostics are broken.

    Mirrored to the local event log unless it is disabled so developers see
    failures inline with events.
    """
    line = _failure_breadcrumb_line(stage, error, extra)

    primary_path = _CONFIG_DIR / _FAILURE_LOG_FILENAME
    primary_failed = False
    try:
        _write_failure_line(primary_path, line)
    except OSError:
        primary_failed = True
    except Exception:
        # Defensive: an unexpected exception in our diagnostics path must not
        # propagate. Treat it the same as an OSError and try the fallback.
        primary_failed = True

    if primary_failed:
        with contextlib.suppress(Exception):
            _write_failure_line(_FALLBACK_FAILURE_LOG_PATH, line)

    _log_debug_line(
        f"failure stage={stage} error_type={type(error).__name__} "
        f"error_message={_scrub_error_message(str(error))!r}"
    )


def _capture_sentry_failure(error: BaseException) -> None:
    """Report telemetry failures without making analytics depend on Sentry imports."""
    try:
        from infrastructure.observability.errors.sentry import capture_exception
    except Exception:
        return
    capture_exception(error)


class _QueueOverflow(RuntimeError):
    """Synthetic exception used so ``queue.Full`` produces a useful breadcrumb."""


class _InvalidPropertyValue(TypeError):
    """Raised internally when a caller submits a property value we cannot serialize."""


class _PayloadTooLarge(ValueError):
    """Raised internally when an event exceeds the ingest contract's body limit."""


def _coerce_properties(
    event: str,
    properties: Properties | None,
) -> Properties:
    """Return a sanitized, JSON-compatible copy of ``properties``.

    Existing analytics queries expect scalar numerics as strings, so preserve
    that contract while allowing nested JSON arrays and objects used by AI
    events. Drop unsupported values with a failure breadcrumb rather than
    crashing the product path.
    """
    if not properties:
        return {}

    coerced: Properties = {}
    for key, value in properties.items():
        if isinstance(value, bool | str):
            coerced[key] = value
        elif value is None:
            continue
        elif isinstance(value, int | float):
            coerced[key] = str(value)
        elif _is_json_value(value):
            coerced[key] = value
        else:
            _log_failure(
                "invalid_property",
                _InvalidPropertyValue(
                    f"property {key!r} has unsupported type {type(value).__name__}"
                ),
                event=event,
                property_key=key,
                value_type=type(value).__name__,
            )
    return coerced


def _is_json_value(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, bool | str | int):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_value(v) for k, v in value.items())
    return False


_COMPOSITE_FINGERPRINT = _build_composite_fingerprint()
_ANALYTICS_RUNTIME = detect_analytics_runtime()

_BASE_PROPERTIES: Final[Properties] = {
    "cli_version": _cli_version(),
    "python_version": platform.python_version(),
    "os_family": platform.system().lower(),
    "os_version": platform.release(),
    "composite_fingerprint": _COMPOSITE_FINGERPRINT.value,
    "composite_fingerprint_version": _COMPOSITE_FINGERPRINT_VERSION,
    "composite_fingerprint_components": _COMPOSITE_FINGERPRINT.components,
    "execution_environment": _ANALYTICS_RUNTIME.execution_environment,
    "is_ci": _ANALYTICS_RUNTIME.is_ci,
    "is_container": _ANALYTICS_RUNTIME.is_container,
    "container_runtime": _ANALYTICS_RUNTIME.container_runtime,
    "$process_person_profile": False,
}


class Analytics:
    def __init__(self) -> None:
        self._disabled = _is_opted_out()
        self._anonymous_id = _get_or_create_anonymous_id()
        self._identity_persistence = _identity_persistence()
        self._queue: queue.Queue[_Envelope | None] = queue.Queue(maxsize=_QUEUE_SIZE)
        self._pending_lock = threading.Lock()
        self._pending = 0
        self._drained = threading.Event()
        self._drained.set()
        self._worker: threading.Thread | None = None
        self._shutdown = False
        self._worker_alive = not self._disabled
        self._persistent_properties: Properties = {}
        self._identified_organization_groups: set[str] = set()
        self._org_group_lock = threading.Lock()
        self._destination: AnalyticsDestination | None = None

        if not self._disabled:
            self._destination = resolve_analytics_destination()

            # Never block interpreter exit on analytics; callers that need a
            # best-effort drain (e.g. install) pass flush=True explicitly.
            def _atexit_shutdown() -> None:
                self.shutdown(flush=False)

            atexit.register(_atexit_shutdown)
            for properties in _pop_user_id_load_failures():
                self.capture(Event.USER_ID_LOAD_FAILED, properties)

    def capture(self, event: Event, properties: Properties | None = None) -> None:
        if self._disabled or self._shutdown:
            return
        merged = merge_usage_enrichment(
            _BASE_PROPERTIES
            | self._persistent_properties
            | _coerce_properties(event.value, properties)
        )
        self._ensure_organization_group(merged)
        envelope = _Envelope(
            event=event.value,
            properties=merged,
            destination=self._destination,
        )
        self._enqueue(envelope)

    def set_persistent_property(self, key: str, value: JsonScalar) -> None:
        """Store a property merged into every subsequent :meth:`capture` call.

        Use for user-scoped attributes discovered after the ``Analytics``
        instance is created (e.g. ``github_username`` after OAuth login) so
        they appear on all future events as direct event properties and are
        trivially queryable without a person-profile join. No-ops when
        telemetry is disabled.
        """
        if self._disabled:
            return
        self._persistent_properties[key] = value

    def refresh_destination(self) -> None:
        """Reload endpoint credentials after account state changes in-process."""
        if self._disabled or self._shutdown:
            return
        self._destination = resolve_analytics_destination()

    def identify(self, set_properties: Properties) -> None:
        """Emit a ``$identify`` control event for downstream identity handling.

        Overrides the project-wide ``$process_person_profile: False`` default for
        this event. No-ops when telemetry is disabled, exactly like
        :meth:`capture`.
        """
        if self._disabled or self._shutdown:
            return
        coerced = _coerce_properties("$identify", set_properties)
        if not coerced:
            return
        properties = merge_usage_enrichment(
            {
                **_BASE_PROPERTIES,
                "$process_person_profile": True,
                "$set": coerced,
            }
        )
        self._ensure_organization_group(properties)
        self._enqueue(
            _Envelope(
                event="$identify",
                properties=properties,
                destination=self._destination,
            )
        )

    def group_identify(
        self,
        group_type: str,
        group_key: str,
        set_properties: Properties | None = None,
    ) -> None:
        """Emit a ``$groupidentify`` control event for downstream group handling.

        Used alongside CLI/gateway events that stamp ``$groups.organization``
        with the same organization key as the webapp integration inventory.
        """
        if self._disabled or self._shutdown:
            return
        key = group_key.strip()
        if not group_type.strip() or not key:
            return
        coerced = _coerce_properties("$groupidentify", set_properties)
        properties: Properties = {
            **_BASE_PROPERTIES,
            "$group_type": group_type,
            "$group_key": key,
            "$group_set": coerced,
        }
        self._enqueue(
            _Envelope(
                event="$groupidentify",
                properties=properties,
                destination=self._destination,
            )
        )

    def _ensure_organization_group(self, properties: Properties) -> None:
        """Emit ``$groupidentify`` once per process for each organization id seen."""
        org = properties.get("organization_id")
        if not isinstance(org, str):
            return
        org_id = org.strip()
        if not org_id:
            return
        with self._org_group_lock:
            if org_id in self._identified_organization_groups:
                return
            self._identified_organization_groups.add(org_id)
        self.group_identify(
            ORGANIZATION_GROUP_TYPE,
            org_id,
            {"organization_id": org_id},
        )

    def _enqueue(self, envelope: _Envelope) -> None:
        pending_registered = False
        try:
            self._ensure_worker()
            with self._pending_lock:
                self._pending += 1
                pending_registered = True
                self._drained.clear()
            self._queue.put_nowait(envelope)
        except queue.Full:
            self._mark_done()
            error = _QueueOverflow(f"queue overflow at size={_QUEUE_SIZE}")
            _log_failure("queue_full", error, event=envelope.event)
            _capture_sentry_failure(error)
        except Exception as exc:
            if pending_registered:
                self._mark_done()
            _log_failure("capture", exc, event=envelope.event)
            _capture_sentry_failure(exc)

    def shutdown(self, *, flush: bool = False, timeout: float = _SHUTDOWN_WAIT) -> None:
        """Stop accepting events and signal the worker to exit.

        By default ``flush=False``: enqueue a sentinel and return immediately so
        interactive exit (``/quit``) is not blocked on network I/O. Pass
        ``flush=True`` only when a short best-effort drain is worth waiting for
        (e.g. one-shot install telemetry).
        """
        if self._shutdown:
            return
        self._shutdown = True
        if self._worker_alive:
            try:
                self._ensure_worker()
            except Exception as exc:
                _log_failure("worker_start", exc)
                _capture_sentry_failure(exc)
                self._worker_alive = False
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        if flush and self._worker is not None:
            # One budget for the whole drain, not one per wait: these run back to
            # back, so passing ``timeout`` to each made a documented 0.5s exit
            # block for 1.0s whenever a send outlived it.
            deadline = time.monotonic() + timeout
            self._drained.wait(timeout=timeout)
            self._worker.join(timeout=max(0.0, deadline - time.monotonic()))

    def _ensure_worker(self) -> None:
        if self._worker is not None:
            return
        worker = threading.Thread(target=self._worker_loop, name="opensre-analytics", daemon=True)
        worker.start()
        self._worker = worker

    def _worker_loop(self) -> None:
        try:
            with httpx.Client(
                timeout=_SEND_TIMEOUT,
                trust_env=False,
            ) as client:
                while True:
                    item = self._queue.get()
                    if item is None:
                        self._queue.task_done()
                        break
                    try:
                        self._send(client, item)
                    finally:
                        self._queue.task_done()
                        self._mark_done()
                while True:
                    try:
                        item = self._queue.get_nowait()
                    except queue.Empty:
                        return
                    try:
                        if item is not None:
                            self._send(client, item)
                    finally:
                        self._queue.task_done()
                        self._mark_done()
        except Exception as exc:
            # SSL/TLS or other fatal client-init errors — log only so the daemon
            # thread exits cleanly without surfacing infrastructure noise to Sentry.
            _log_failure("worker_loop_fatal", exc)

    def _send(self, client: httpx.Client, item: _Envelope) -> None:
        destination = item.destination
        if destination is None:
            return
        properties: Properties = {
            **item.properties,
            "distinct_id": self._anonymous_id,
            "$lib": "opensre-cli",
            "identity_persistence": self._identity_persistence,
        }
        insert_id = _event_insert_id(item.event, self._anonymous_id)
        if insert_id is not None:
            properties["$insert_id"] = insert_id
        _log_event_line(item.event, properties)
        payload = {
            "schema_version": ANALYTICS_EVENT_SCHEMA_VERSION,
            "event_id": insert_id or item.event_id,
            "occurred_at": item.occurred_at,
            "source": ANALYTICS_SOURCE,
            "anonymous_id": self._anonymous_id,
            "event": item.event,
            "properties": properties,
        }
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(body) > ANALYTICS_MAX_PAYLOAD_BYTES:
            _log_failure(
                "analytics_send",
                _PayloadTooLarge(
                    f"payload has {len(body)} bytes; limit is {ANALYTICS_MAX_PAYLOAD_BYTES}"
                ),
                event=item.event,
            )
            return
        try:
            client.post(
                destination.endpoint_url,
                content=body,
                headers=destination.headers(body),
            ).raise_for_status()
        except httpx.TransportError as exc:
            # Network/TLS failures (ConnectTimeout, ConnectError, ReadTimeout, …) are
            # transient infrastructure issues, not application bugs — log only.
            _log_failure("analytics_send", exc, event=item.event)
        except httpx.HTTPStatusError as exc:
            # Webapp HTTP errors (4xx contract/auth issues, 5xx storage failures)
            # are not application bugs — log only, do not surface to Sentry.
            _log_failure("analytics_send", exc, event=item.event)
        except Exception as exc:
            _log_failure("analytics_send", exc, event=item.event)
            _capture_sentry_failure(exc)

    def _mark_done(self) -> None:
        with self._pending_lock:
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                self._drained.set()


_instance: Analytics | None = None


def get_analytics() -> Analytics:
    global _instance
    if _instance is None:
        _instance = Analytics()
    return _instance


def shutdown_analytics(*, flush: bool = False, timeout: float = _SHUTDOWN_WAIT) -> None:
    if _instance is not None:
        _instance.shutdown(flush=flush, timeout=timeout)


def analytics_needs_flush() -> bool:
    """True when a best-effort drain would wait on queued or in-flight events.

    ``_pending`` is raised in ``capture`` before enqueue and lowered in
    ``_mark_done`` only after the POST completes, so it already covers both
    queued and in-flight events; an idle-but-alive worker does not need a drain.
    """
    if _instance is None or _instance._disabled or _instance._shutdown:
        return False
    return _instance._pending > 0


def capture_install_detected_if_needed(properties: Properties | None = None) -> bool:
    """Capture ``install_detected`` once per persisted OpenSRE home."""
    if _path_exists(_FIRST_RUN_PATH):
        return False
    analytics = get_analytics()
    if not _touch_once(_FIRST_RUN_PATH):
        return False
    analytics.capture(Event.INSTALL_DETECTED, properties)
    return True


def capture_first_run_if_needed() -> None:
    capture_install_detected_if_needed()
