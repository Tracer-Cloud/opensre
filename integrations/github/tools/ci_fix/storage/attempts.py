"""Durable prepared pushes used to resume verification after a worker crash."""

import json
from dataclasses import asdict, dataclass, replace

from integrations.github.tools.ci_fix.storage import database


@dataclass(frozen=True)
class PreparedPush:
    source_head_sha: str
    fix_head_sha: str
    branch_name: str
    changed_files: list[str]
    checks_state: str = ""


def save_prepared_push(key: str, prepared: PreparedPush) -> None:
    """Commit intent before pushing, so a successful push is discoverable after a crash."""
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO prepared_pushes (target, payload) VALUES (?, ?) "
            "ON CONFLICT(target) DO UPDATE SET payload = excluded.payload",
            (key, json.dumps(asdict(prepared))),
        )


def load_prepared_push(key: str) -> PreparedPush | None:
    """Read the latest prepared push for a repository target."""
    if not database.database_path().exists():
        return None
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT payload FROM prepared_pushes WHERE target = ?", (key,)
        ).fetchone()
    return PreparedPush(**json.loads(row[0])) if row is not None else None


def record_unsettled_merge(key: str, revision: str) -> int:
    """Count one more unsettled merge of *revision*; any other revision starts again at one."""
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT revision, attempts FROM unsettled_merge_revisions WHERE target = ?", (key,)
        ).fetchone()
        attempts = row[1] + 1 if row is not None and row[0] == revision else 1
        conn.execute(
            "INSERT INTO unsettled_merge_revisions (target, revision, attempts) VALUES (?, ?, ?) "
            "ON CONFLICT(target) DO UPDATE SET revision = excluded.revision, "
            "attempts = excluded.attempts",
            (key, revision, attempts),
        )
    return int(attempts)


def repair_key(owner: str, repo: str, target: str) -> str:
    return json.dumps((owner.casefold(), repo.casefold(), target))


def record_verification(key: str, fix_sha: str, state: str) -> None:
    """Retain successful verification and let a terminal failed repair be attempted again."""
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT payload FROM prepared_pushes WHERE target = ?", (key,)
        ).fetchone()
        if row is None:
            return
        prepared = PreparedPush(**json.loads(row[0]))
        if prepared.fix_head_sha == fix_sha:
            payload = json.dumps(asdict(replace(prepared, checks_state=state)))
            conn.execute("UPDATE prepared_pushes SET payload = ? WHERE target = ?", (payload, key))
