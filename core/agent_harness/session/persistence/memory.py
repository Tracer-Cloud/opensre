"""In-memory v2 session storage backend."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

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
                "cwd": "",
            }
        ]

    def append_turn(self, session: SessionPersistenceSource, kind: str, text: str) -> None:
        self._append(
            session.session_id,
            "custom_message",
            {"custom_type": "turn_stub", "kind": kind, "text": text, "display": False},
        )

    def append_session_name(self, session_id: str, name: str) -> str:
        return self._append(
            session_id,
            "custom_message",
            {"custom_type": "session_name", "name": name},
            sidecar=True,
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

    def append_custom_message(
        self,
        session_id: str,
        *,
        custom_type: str,
        content: Any,
        display: bool = True,
    ) -> str:
        return self._append(
            session_id,
            "custom_message",
            {"custom_type": custom_type, "content": content, "display": display},
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
        replacement_messages: list[list[str]] | None = None,
        replacement_evidence: list[dict[str, Any]] | None = None,
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
                # What the compaction kept verbatim. Present means the record
                # replaces everything before it when the session is restored.
                **(
                    {"replacement_messages": replacement_messages}
                    if replacement_messages is not None
                    else {}
                ),
                **(
                    {"replacement_evidence": replacement_evidence}
                    if replacement_evidence is not None
                    else {}
                ),
            },
        )

    def flush(self, session: SessionPersistenceSource) -> None:
        records = self._files.get(session.session_id)
        if not records:
            return
        trailing_leaf = self._conversation_tip_is_closed(records)
        if not trailing_leaf and not any(rec.get("type") != "session" for rec in records):
            from core.agent_harness.session.pending_choice import PendingUserChoice

            if not isinstance(getattr(session, "pending_user_choice", None), PendingUserChoice):
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
        self._append_session_goal_state(session, records)
        records = self._files.get(session.session_id, records)
        self._append_task_plan_state(session, records)
        records = self._files.get(session.session_id, records)
        if hasattr(session, "pending_user_choice"):
            from core.agent_harness.session.pending_choice import (
                PENDING_USER_CHOICE_STATE_CUSTOM_TYPE,
                pending_user_choice_state_snapshot,
                should_persist_pending_user_choice_state,
            )

            choice_state = pending_user_choice_state_snapshot(session)
            if should_persist_pending_user_choice_state(choice_state, prior_records=records):
                self._append(
                    session.session_id,
                    "custom_message",
                    {
                        "custom_type": PENDING_USER_CHOICE_STATE_CUSTOM_TYPE,
                        "content": choice_state or {},
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

    def flush_session_goal_control_state(self, session: SessionPersistenceSource) -> None:
        """Persist goal and task-plan state changed by a goal control."""
        from core.agent_harness.session.persistence.contracts import (
            SESSION_GOAL_CONTROL_STATE_CUSTOM_TYPE,
        )
        from core.agent_harness.session_goal.persist import session_goal_state_snapshot
        from core.agent_harness.task_plan.persist import task_plan_state_snapshot

        records = self._files.get(session.session_id)
        if records is None:
            raise FileNotFoundError(session.session_id)
        self._append(
            session.session_id,
            "custom_message",
            {
                "custom_type": SESSION_GOAL_CONTROL_STATE_CUSTOM_TYPE,
                "content": {
                    "session_goal_state": session_goal_state_snapshot(session),
                    "task_plan_state": task_plan_state_snapshot(session) or {},
                },
                "display": False,
            },
        )
        self._append_task_plan_state(session, records)
        records = self._files.get(session.session_id, records)
        self._append_session_goal_state(session, records)

    def append_session_goal_control(self, session_id: str, reason: str) -> str:
        """Durably record a goal control outside the live conversation branch."""
        from core.agent_harness.session_goal.persist import (
            SESSION_GOAL_CONTROL_RECORD_TYPE,
            SESSION_GOAL_CONTROL_REQUESTED,
        )

        records = self._files.get(session_id)
        if records is None:
            raise OSError("Could not persist session-goal control")
        target_entry_id = self._current_leaf(records)
        if target_entry_id is None:
            raise OSError("Could not identify session-goal control branch")
        control_id = uuid.uuid4().hex
        entry_id = self._append(
            session_id,
            SESSION_GOAL_CONTROL_RECORD_TYPE,
            {
                "control_id": control_id,
                "reason": reason,
                "status": SESSION_GOAL_CONTROL_REQUESTED,
                "target_entry_id": target_entry_id,
            },
            sidecar=True,
        )
        if not entry_id:
            raise OSError("Could not persist session-goal control")
        return control_id

    def complete_session_goal_control(self, session_id: str, control_id: str) -> None:
        """Acknowledge a previously recorded goal control."""
        from core.agent_harness.session_goal.persist import (
            SESSION_GOAL_CONTROL_APPLIED,
            SESSION_GOAL_CONTROL_RECORD_TYPE,
        )

        entry_id = self._append(
            session_id,
            SESSION_GOAL_CONTROL_RECORD_TYPE,
            {
                "control_id": control_id,
                "status": SESSION_GOAL_CONTROL_APPLIED,
            },
            sidecar=True,
        )
        if not entry_id:
            raise OSError("Could not acknowledge session-goal control")

    def _append_session_goal_state(
        self,
        session: SessionPersistenceSource,
        records: list[dict[str, Any]],
    ) -> None:
        if not hasattr(session, "session_goal"):
            return
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

    def _append_task_plan_state(
        self,
        session: SessionPersistenceSource,
        records: list[dict[str, Any]],
    ) -> None:
        if not hasattr(session, "task_plan"):
            return
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

    @staticmethod
    def _conversation_tip_is_closed(records: list[dict[str, Any]]) -> bool:
        """Return whether the newest non-sidecar record is a closing leaf."""
        for record in reversed(records):
            if record.get("sidecar"):
                continue
            return record.get("type") == "leaf"
        return False
