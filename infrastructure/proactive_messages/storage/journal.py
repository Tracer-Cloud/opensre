"""Crash-safe append-only proactive decision ledger and judgement cursor."""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from infrastructure.proactive_messages.models import ProactiveMessageDecision
from infrastructure.proactive_messages.storage.paths import (
    decision_ledger_path,
    judgement_cursor_path,
)

_LOCK_TIMEOUT_SECONDS = 10


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DecisionLedger:
    """Append-only send/suppress decisions and Slack delivery outcomes."""

    def record_decision(
        self,
        *,
        session_id: str,
        interaction_id: str,
        end_record_id: str,
        policy_name: str,
        policy_version: int,
        decision: ProactiveMessageDecision,
        signal_fingerprint: str,
        channel_id: str,
        thread_ts: str,
        signal_fingerprint_version: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Append a decision once per interaction; return ``(record, created)``."""
        path = decision_ledger_path()
        with _lock(path):
            events = _read_jsonl(path)
            existing = _decision_for_interaction(events, interaction_id)
            if existing is not None:
                return _merge_delivery(existing, events), False
            record = {
                "type": "decision",
                "decision_id": uuid.uuid4().hex,
                "created_at": _now(),
                "session_id": session_id,
                "interaction_id": interaction_id,
                "end_record_id": end_record_id,
                "policy_name": policy_name,
                "policy_version": policy_version,
                "decision": decision.decision,
                "rationale": decision.rationale,
                "message": decision.message if decision.decision == "send" else "",
                "signal_key": decision.signal_key.strip().casefold(),
                "signal_state": decision.signal_state,
                "signal_fingerprint": signal_fingerprint,
                "verified_information": decision.verified_information,
                "evidence_quote": decision.evidence_quote,
                "evidence_source": decision.evidence_source,
                "owner": decision.owner,
                "next_action": decision.next_action,
                "material_timing_reason": decision.material_timing_reason,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "delivery_status": "pending" if decision.decision == "send" else "suppressed",
            }
            if signal_fingerprint_version is not None:
                record["signal_fingerprint_version"] = signal_fingerprint_version
            _append_jsonl(path, record)
            return dict(record), True

    def record_delivery(
        self,
        decision_id: str,
        *,
        status: str,
        slack_message_ts: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """Append the terminal Slack delivery outcome for one send decision."""
        path = decision_ledger_path()
        with _lock(path):
            record = {
                "type": "delivery",
                "decision_id": decision_id,
                "created_at": _now(),
                "delivery_status": status,
                "slack_message_ts": slack_message_ts,
                "error_type": error_type,
            }
            _append_jsonl(path, record)

    def for_interaction(self, interaction_id: str) -> dict[str, Any] | None:
        """Return a merged ledger row for ``interaction_id`` when already judged."""
        events = _read_jsonl(decision_ledger_path())
        decision = _decision_for_interaction(events, interaction_id)
        return _merge_delivery(decision, events) if decision is not None else None

    def has_delivered_signal(
        self,
        *,
        signal_key: str,
        signal_fingerprint: str,
        signal_fingerprint_version: int,
        legacy_signal_fingerprint: str,
    ) -> bool:
        """Whether the same unchanged signal was successfully delivered earlier."""
        events = _read_jsonl(decision_ledger_path())
        normalized_key = signal_key.strip().casefold()
        delivered_ids = {
            event.get("decision_id")
            for event in events
            if event.get("type") == "delivery" and event.get("delivery_status") == "delivered"
        }
        for record in events:
            if record.get("type") != "decision" or record.get("decision") != "send":
                continue
            if record.get("decision_id") not in delivered_ids:
                continue
            if not normalized_key or record.get("signal_key") != normalized_key:
                continue
            recorded_version = record.get("signal_fingerprint_version")
            if recorded_version == signal_fingerprint_version:
                if signal_fingerprint and record.get("signal_fingerprint") == signal_fingerprint:
                    return True
                continue
            if recorded_version is not None:
                continue
            legacy_match = (
                legacy_signal_fingerprint
                and record.get("signal_fingerprint") == legacy_signal_fingerprint
            )
            if legacy_match:
                return True
        return False

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent merged decision rows, newest first."""
        bounded_limit = max(1, min(int(limit), 50))
        events = _read_jsonl(decision_ledger_path())
        decisions = [event for event in events if event.get("type") == "decision"]
        return [
            _merge_delivery(decision, events) for decision in reversed(decisions[-bounded_limit:])
        ]


class JudgementCursor:
    """Last evaluated durable session record, independently per session."""

    def last(self, session_id: str) -> dict[str, Any] | None:
        """Return the newest cursor row for ``session_id``."""
        for record in reversed(_read_jsonl(judgement_cursor_path())):
            if record.get("session_id") == session_id:
                return record
        return None

    def advance(
        self,
        *,
        session_id: str,
        interaction_id: str,
        end_record_id: str,
    ) -> None:
        """Durably advance a session cursor without duplicating its current value."""
        path = judgement_cursor_path()
        with _lock(path):
            records = _read_jsonl(path)
            latest = next(
                (record for record in reversed(records) if record.get("session_id") == session_id),
                None,
            )
            if latest is not None and latest.get("end_record_id") == end_record_id:
                return
            _append_jsonl(
                path,
                {
                    "type": "cursor",
                    "created_at": _now(),
                    "session_id": session_id,
                    "interaction_id": interaction_id,
                    "end_record_id": end_record_id,
                },
            )


def _lock(path: Path) -> FileLock:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return FileLock(f"{path}.lock", timeout=_LOCK_TIMEOUT_SECONDS)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with contextlib.suppress(OSError):
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(json.JSONDecodeError):
                record = json.loads(line)
                if isinstance(record, dict):
                    records.append(record)
        return records
    return []


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        raise


def _decision_for_interaction(
    events: list[dict[str, Any]], interaction_id: str
) -> dict[str, Any] | None:
    return next(
        (
            event
            for event in reversed(events)
            if event.get("type") == "decision" and event.get("interaction_id") == interaction_id
        ),
        None,
    )


def _merge_delivery(decision: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(decision)
    decision_id = decision.get("decision_id")
    for event in reversed(events):
        if event.get("type") == "delivery" and event.get("decision_id") == decision_id:
            merged.update(
                {
                    key: value
                    for key, value in event.items()
                    if key not in {"type", "decision_id", "created_at"} and value is not None
                }
            )
            break
    return merged


__all__ = ["DecisionLedger", "JudgementCursor"]
