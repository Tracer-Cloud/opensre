from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from config.constants import paths
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness import session_path
from infrastructure.proactive_messages import ProactiveTrigger


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)


@pytest.fixture
def scope() -> StorageScope:
    return StorageScope(principal=Principal.org("org_proactive"), actor=Actor("U_PROACTIVE"))


def write_interaction(
    scope: StorageScope,
    *,
    session_id: str,
    suffix: str = "one",
    user: str = "Review the merged CI run.",
    assistant: str = "The merged run shows flaky test test_retry failed on attempt 1.",
) -> ProactiveTrigger:
    records: list[dict[str, Any]] = [
        {"type": "session", "version": 2, "id": session_id},
        {
            "type": "message",
            "id": f"before-{suffix}",
            "parent_id": None,
            "role": "assistant",
            "content": "Earlier turn",
        },
        {
            "type": "message",
            "id": f"user-{suffix}",
            "parent_id": f"before-{suffix}",
            "role": "user",
            "content": user,
        },
        {
            "type": "message",
            "id": f"assistant-{suffix}",
            "parent_id": f"user-{suffix}",
            "role": "assistant",
            "content": assistant,
        },
        {
            "type": "leaf",
            "id": f"end-{suffix}",
            "parent_id": f"assistant-{suffix}",
        },
    ]
    with bound_storage_scope(scope):
        path = session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
    return ProactiveTrigger(
        session_id=session_id,
        start_record_id=f"before-{suffix}",
        end_record_id=f"end-{suffix}",
        channel_id="C12345678",
        thread_ts="100.1",
        user_id=scope.actor.id,
    )


__all__ = ["write_interaction"]
