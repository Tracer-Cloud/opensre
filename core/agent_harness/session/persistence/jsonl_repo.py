"""JSONL-backed v2 session-tree repository."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import core.agent_harness.session.persistence.paths as storage_paths
from config.constants.session_store import WORKING_DIRECTORY_STATE_CUSTOM_TYPE
from core.agent_harness.session.persistence.contracts import (
    CHAT_KINDS,
    RestoreContextKey,
)
from core.agent_harness.session.persistence.wal_recovery import dangling_tool_intents
from core.state.transcript_window import SESSION_SUMMARY_PREFIX


class JsonlSessionRepo:
    """Read-only queries over v2 session files."""

    def load_recent(self, n: int = 20) -> list[dict[str, Any]]:
        root = storage_paths.sessions_dir()
        if not root.exists():
            return []

        results: list[dict[str, Any]] = []
        for path in sorted(root.glob("*.jsonl"), key=_mtime, reverse=True):
            with contextlib.suppress(Exception):
                loaded = _load_v2_file(path)
                if loaded is None:
                    continue
                header, entries = loaded
                results.append(self._summary(path, header, entries))
            if len(results) >= n:
                break
        results.sort(key=lambda x: x.get("started_at") or "", reverse=True)
        return results[:n]

    def count_prefix_matches(self, prefix: str) -> int:
        root = storage_paths.sessions_dir()
        if not root.exists():
            return 0
        session_prefix, _entry_id = _split_session_ref(prefix)
        count = 0
        for path in root.glob("*.jsonl"):
            if not path.stem.startswith(session_prefix):
                continue
            if _load_v2_file(path) is not None:
                count += 1
        return count

    def load_session(self, session_id_prefix: str) -> dict[str, Any] | None:
        root = storage_paths.sessions_dir()
        if not root.exists():
            return None

        session_prefix, entry_ref = _split_session_ref(session_id_prefix)
        target_path: Path | None = None
        for path in root.glob("*.jsonl"):
            if not path.stem.startswith(session_prefix):
                continue
            if _load_v2_file(path) is None:
                continue
            if target_path is not None:
                return None
            target_path = path

        if target_path is None:
            return None

        with contextlib.suppress(Exception):
            loaded = _load_v2_file(target_path)
            if loaded is None:
                return None
            header, entries = loaded
            target_entry = _resolve_entry_id(entries, entry_ref)
            branch = _branch_to(entries, target_entry)
            messages = _messages_for_branch(branch)
            context = _accumulated_context_for_branch(branch)
            goal_state = _session_goal_state_for_branch(branch)
            plan_state = _task_plan_state_for_branch(branch)
            history = _history_for_branch(branch)
            turn_details = _turn_details_for_branch(branch)
            return {
                "session_id": str(header.get("id") or target_path.stem),
                "entry_id": target_entry,
                "leaf_id": _resolve_entry_id(entries, None),
                "name": storage_paths.derive_name(_records_to_lines([header, *entries])),
                "started_at": header.get("created_at"),
                RestoreContextKey.CLI_AGENT_MESSAGES: messages,
                RestoreContextKey.ACCUMULATED_CONTEXT: context,
                RestoreContextKey.SESSION_GOAL_STATE: goal_state,
                RestoreContextKey.TASK_PLAN_STATE: plan_state,
                RestoreContextKey.HISTORY: history,
                RestoreContextKey.WORKING_DIRECTORY: _working_directory_for_branch(
                    header,
                    branch,
                ),
                "turn_details": turn_details,
                "has_snapshot": False,
                # WAL sidecars are off-branch, so scan the full entry list:
                # intents with no commit are tool calls that were in flight
                # when the session ended (crash / kill mid-turn).
                "dangling_tool_intents": dangling_tool_intents(entries),
            }
        return None

    @staticmethod
    def _summary(
        path: Path, header: dict[str, Any], entries: list[dict[str, Any]]
    ) -> dict[str, Any]:
        leaf = next((rec for rec in reversed(entries) if rec.get("type") == "leaf"), None)
        total_turns = _count_turns(entries)
        return {
            "session_id": str(header.get("id") or path.stem),
            "name": storage_paths.derive_name(_records_to_lines([header, *entries])),
            "started_at": header.get("created_at"),
            "opensre_version": header.get("opensre_version"),
            "duration_secs": leaf.get("duration_secs") if leaf else None,
            "total_turns": leaf.get("total_turns") if leaf else total_turns,
            "chat_turns": leaf.get("chat_turns") if leaf else _count_chat_turns(entries),
            "is_ended": leaf is not None,
            "has_snapshot": any(
                rec.get("type") == "message"
                or (
                    rec.get("type") == "custom_message"
                    and rec.get("custom_type") == "accumulated_context"
                )
                for rec in entries
            ),
            "leaf_id": _resolve_entry_id(entries, None),
        }


def _mtime(path: Path) -> float:
    with contextlib.suppress(OSError):
        return path.stat().st_mtime
    return 0.0


def _split_session_ref(ref: str) -> tuple[str, str | None]:
    if ":" not in ref:
        return ref, None
    session_prefix, entry_ref = ref.split(":", 1)
    return session_prefix, entry_ref or None


def _load_v2_file(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    return _load_v2_lines(path.read_text(encoding="utf-8").splitlines())


def _load_v2_lines(lines: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    if not lines:
        return None
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(header, dict)
        or header.get("type") != "session"
        or header.get("version") != 2
    ):
        return None
    entries: list[dict[str, Any]] = []
    for line in lines[1:]:
        with contextlib.suppress(json.JSONDecodeError):
            rec = json.loads(line)
            if isinstance(rec, dict) and "id" in rec and "type" in rec:
                entries.append(rec)
    return header, entries


def _resolve_entry_id(entries: list[dict[str, Any]], entry_ref: str | None) -> str | None:
    if entry_ref:
        matches = [
            str(rec.get("id")) for rec in entries if str(rec.get("id", "")).startswith(entry_ref)
        ]
        return matches[0] if len(matches) == 1 else entry_ref
    for rec in reversed(entries):
        rec_type = rec.get("type")
        # WAL intents/commits are sidecars like trace spans: a trailing one
        # must not be mistaken for the conversation tip (its parent chain is
        # detached, so resuming from it would drop the whole branch).
        if rec_type == "trace_span" or rec.get("sidecar"):
            continue
        if rec_type == "leaf":
            parent = str(rec.get("parent_id") or "")
            return parent or None
        return str(rec.get("id") or "") or None
    return None


def _branch_to(entries: list[dict[str, Any]], target_id: str | None) -> list[dict[str, Any]]:
    if target_id is None:
        return []
    by_id = {str(rec.get("id")): rec for rec in entries if rec.get("id")}
    out: list[dict[str, Any]] = []
    current = target_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        rec = by_id.get(current)
        if rec is None:
            break
        if rec.get("type") != "leaf":
            out.append(rec)
        current = str(rec.get("parent_id") or "")
    return list(reversed(out))


def _messages_for_branch(branch: list[dict[str, Any]]) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    for rec in branch:
        if rec.get("type") == "compaction":
            summary = str(rec.get("summary") or "").strip()
            if summary:
                messages.append(("assistant", f"{SESSION_SUMMARY_PREFIX}{summary}"))
            continue
        if rec.get("type") != "message":
            continue
        role = str(rec.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = str(rec.get("content") or "")
        if content:
            messages.append((role, content))
    return messages


def _history_for_branch(branch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for rec in branch:
        if rec.get("type") == "custom_message" and rec.get("custom_type") == "turn_stub":
            history.append(
                {
                    "kind": rec.get("kind", ""),
                    "text": rec.get("text") or "",
                    "ok": True,
                    "timestamp": rec.get("timestamp"),
                }
            )
    return history


def _working_directory_for_branch(
    header: dict[str, Any],
    branch: list[dict[str, Any]],
) -> str | None:
    value = header.get("cwd")
    for record in branch:
        if (
            record.get("type") == "custom_message"
            and record.get("custom_type") == WORKING_DIRECTORY_STATE_CUSTOM_TYPE
        ):
            value = record.get("content")
    return value if isinstance(value, str) and value.strip() else None


def _turn_details_for_branch(branch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    pending_user: dict[str, Any] | None = None
    for rec in branch:
        if rec.get("type") != "message":
            continue
        raw_metadata = rec.get("metadata")
        metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        role = rec.get("role")
        if role == "user":
            pending_user = {
                "kind": metadata.get("kind", "chat"),
                "prompt": rec.get("content") or "",
            }
            pending_user.update(metadata)
        elif role == "assistant" and pending_user is not None:
            pending_user["response"] = rec.get("content") or ""
            details.append(pending_user)
            pending_user = None
    if pending_user is not None:
        details.append(pending_user)
    return details


def _accumulated_context_for_branch(branch: list[dict[str, Any]]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for rec in branch:
        if rec.get("type") == "custom_message" and rec.get("custom_type") == "accumulated_context":
            content = rec.get("content")
            if isinstance(content, dict):
                context.update(content)
    return context


def _session_goal_state_for_branch(branch: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the last ``session_goal_state`` custom message on the branch."""
    from core.agent_harness.session_goal.persist import SESSION_GOAL_STATE_CUSTOM_TYPE

    latest: dict[str, Any] | None = None
    for rec in branch:
        if rec.get("type") != "custom_message":
            continue
        if rec.get("custom_type") != SESSION_GOAL_STATE_CUSTOM_TYPE:
            continue
        content = rec.get("content")
        if isinstance(content, dict):
            latest = content
    return latest


def _task_plan_state_for_branch(branch: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the last ``task_plan_state`` custom message on the branch."""
    from core.agent_harness.task_plan.persist import TASK_PLAN_STATE_CUSTOM_TYPE

    latest: dict[str, Any] | None = None
    for rec in branch:
        if rec.get("type") != "custom_message":
            continue
        if rec.get("custom_type") != TASK_PLAN_STATE_CUSTOM_TYPE:
            continue
        content = rec.get("content")
        if isinstance(content, dict):
            latest = content
    return latest


def _count_turns(entries: list[dict[str, Any]]) -> int:
    return sum(
        1
        for rec in entries
        if rec.get("type") == "custom_message" and rec.get("custom_type") == "turn_stub"
    )


def _count_chat_turns(entries: list[dict[str, Any]]) -> int:
    return sum(
        1
        for rec in entries
        if rec.get("type") == "custom_message"
        and rec.get("custom_type") == "turn_stub"
        and rec.get("kind") in CHAT_KINDS
    )


def _records_to_lines(records: list[dict[str, Any]]) -> list[str]:
    return [json.dumps(rec, ensure_ascii=False, default=str) for rec in records]
