"""In-memory v2 session storage backend."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from config.constants.session_store import WORKING_DIRECTORY_STATE_CUSTOM_TYPE
from core.agent_harness.session.persistence.contracts import SessionPersistenceSource


def _now() -> str:
    return datetime.now(UTC).isoformat()


class InMemorySessionStore:
    """SessionStore backend that stores v2 records in process memory."""

    def __init__(self) -> None:
        self._files: dict[str, list[dict[str, Any]]] = {}

    def read(self, session_id: str) -> list[dict[str, Any]]:
        return [dict(rec) for rec in self._files.get(session_id, [])]

    def open_session(self, session: SessionPersistenceSource) -> None:
        self._files[session.session_id] = [
            {
                "type": "session",
                "version": 2,
                "id": session.session_id,
                "created_at": datetime.fromtimestamp(session.started_at, tz=UTC).isoformat(),
                "cwd": session.working_directory,
            }
        ]

    def append_turn(self, session: SessionPersistenceSource, kind: str, text: str) -> None:
        self._append(
            session.session_id,
            "custom_message",
            {"custom_type": "turn_stub", "kind": kind, "text": text, "display": False},
        )

    def append_working_directory(self, session_id: str, working_directory: str) -> None:
        self._append(
            session_id,
            "custom_message",
            {
                "custom_type": WORKING_DIRECTORY_STATE_CUSTOM_TYPE,
                "content": working_directory,
                "display": False,
            },
        )

    def append_turn_detail(
        self,
        session_id: str,
        kind: str,
        prompt: str,
        *,
        response: str | None = None,
        turn_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        latency_ms: int | None = None,
        system_prompt: str | None = None,
    ) -> None:
        metadata = {
            key: value
            for key, value in {
                "kind": kind,
                "turn_id": turn_id,
                "model": model,
                "provider": provider,
                "latency_ms": latency_ms,
                "system_prompt": system_prompt,
            }.items()
            if value is not None
        }
        self._append(
            session_id, "message", {"role": "user", "content": prompt, "metadata": metadata}
        )
        if response:
            self._append(
                session_id,
                "message",
                {"role": "assistant", "content": response, "metadata": metadata},
            )

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> str:
        return self._append(
            session_id,
            "message",
            {
                "role": role,
                "content": content,
                "metadata": dict(metadata or {}),
            },
            parent_id=parent_id,
        )

    def append_tool_call(
        self,
        session_id: str,
        *,
        tool: str,
        arguments: dict[str, Any],
        result: str,
        ok: bool,
        source: str | None = None,
        tool_call_id: str | None = None,
        sidecar: bool = False,
    ) -> None:
        call_id = self._append(
            session_id,
            "tool_call",
            {"tool": tool, "arguments": arguments, "source": source, "tool_call_id": tool_call_id},
            sidecar=sidecar,
        )
        self._append(
            session_id,
            "tool_result",
            {
                "tool": tool,
                "ok": ok,
                "content": result,
                "source": source,
                "tool_call_id": tool_call_id,
            },
            parent_id=call_id,
            sidecar=sidecar,
        )

    def append_tool_intent(
        self,
        session_id: str,
        *,
        tool: str,
        arguments: dict[str, Any],
        tool_call_id: str,
        seq: int,
        user_text: str | None = None,
    ) -> str:
        return self._append(
            session_id,
            "tool_intent",
            {
                "tool": tool,
                "arguments": arguments,
                "tool_call_id": tool_call_id,
                "seq": seq,
                "user_text": user_text or None,
            },
            sidecar=True,
        )

    def append_tool_update(
        self,
        session_id: str,
        *,
        tool: str,
        update: Any,
        tool_call_id: str | None = None,
    ) -> str:
        return self._append(
            session_id,
            "tool_update",
            {"tool": tool, "update": update, "tool_call_id": tool_call_id},
        )

    def append_compaction(
        self,
        session_id: str,
        *,
        summary: str,
        first_kept_entry_id: str,
        before_chars: int,
        after_chars: int,
        before_tokens: int | None = None,
        after_tokens: int | None = None,
    ) -> str:
        return self._append(
            session_id,
            "compaction",
            {
                "summary": summary,
                "first_kept_entry_id": first_kept_entry_id,
                "before_chars": before_chars,
                "after_chars": after_chars,
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
            },
        )

    def flush(self, session: SessionPersistenceSource) -> None:
        records = self._files.get(session.session_id)
        if not records:
            return
        trailing_leaf = records[-1].get("type") == "leaf"
        if not trailing_leaf and not any(rec.get("type") != "session" for rec in records):
            del self._files[session.session_id]
            return
        # A trailing ``leaf`` means a prior flush already closed the tip. Still
        # allow live state (session goals) to append past that marker so
        # mid-session ``/goal pause`` survives the next gateway ``resolve``;
        # never write a second leaf (flush stays idempotent for end-of-session).
        if session.accumulated_context and not trailing_leaf:
            self._append(
                session.session_id,
                "custom_message",
                {
                    "custom_type": "accumulated_context",
                    "content": dict(session.accumulated_context),
                    "display": False,
                },
            )
            records = self._files.get(session.session_id, records)
        if hasattr(session, "session_goal"):
            from core.agent_harness.session_goal.persist import (
                SESSION_GOAL_STATE_CUSTOM_TYPE,
                session_goal_state_snapshot,
                should_persist_session_goal_state,
            )

            goal_state = session_goal_state_snapshot(session)
            if should_persist_session_goal_state(goal_state, prior_records=records):
                self._append(
                    session.session_id,
                    "custom_message",
                    {
                        "custom_type": SESSION_GOAL_STATE_CUSTOM_TYPE,
                        "content": goal_state,
                        "display": False,
                    },
                )
                records = self._files.get(session.session_id, records)
        if hasattr(session, "task_plan"):
            from core.agent_harness.task_plan.persist import (
                TASK_PLAN_STATE_CUSTOM_TYPE,
                should_persist_task_plan_state,
                task_plan_state_snapshot,
            )

            plan_state = task_plan_state_snapshot(session)
            if should_persist_task_plan_state(plan_state, prior_records=records):
                self._append(
                    session.session_id,
                    "custom_message",
                    {
                        "custom_type": TASK_PLAN_STATE_CUSTOM_TYPE,
                        "content": plan_state or {},
                        "display": False,
                    },
                )
                records = self._files.get(session.session_id, records)
        if trailing_leaf:
            return
        if session.agent.messages and not any(rec.get("type") == "message" for rec in records):
            for role, content in session.agent.messages:
                self._append(
                    session.session_id,
                    "message",
                    {"role": role, "content": content, "metadata": {"kind": "chat"}},
                )
            records = self._files.get(session.session_id, records)
        self._append(
            session.session_id,
            "leaf",
            {
                "total_turns": sum(
                    1
                    for rec in records
                    if rec.get("type") == "custom_message" and rec.get("custom_type") == "turn_stub"
                )
            },
        )

    def reopen_session(self, _session_id: str) -> None:
        return

    def _append(
        self,
        session_id: str,
        entry_type: str,
        payload: dict[str, Any],
        *,
        parent_id: str | None = None,
        sidecar: bool = False,
    ) -> str:
        records = self._files.get(session_id)
        if records is None:
            return ""
        entry_id = uuid.uuid4().hex
        parent = parent_id
        if parent is None and not sidecar:
            parent = self._current_leaf(records)
        records.append(
            {
                "id": entry_id,
                "parent_id": parent,
                "timestamp": _now(),
                "type": entry_type,
                **({"sidecar": True} if sidecar else {}),
                **{key: value for key, value in payload.items() if value is not None},
            }
        )
        return entry_id

    @staticmethod
    def _current_leaf(records: list[dict[str, Any]]) -> str | None:
        for rec in reversed(records):
            if rec.get("sidecar"):
                continue
            if rec.get("type") == "leaf":
                return str(rec.get("parent_id") or "") or None
            if rec.get("type") != "session":
                return str(rec.get("id") or "") or None
        return None
