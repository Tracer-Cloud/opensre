"""Prepared repair intent survives competing writers and failed transactions."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from integrations.github.tools.ci_fix.storage import database
from integrations.github.tools.ci_fix.storage.attempts import (
    PreparedPush,
    load_prepared_push,
    record_verification,
    save_prepared_push,
)


def test_journal_startup_is_concurrent_and_failed_writes_roll_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(database, "database_path", lambda: tmp_path / "repairs.db")

    def prepare(index: int) -> None:
        save_prepared_push(str(index), PreparedPush("source", str(index), "branch", ["fix.py"]))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(prepare, range(4)))
    assert all(load_prepared_push(str(index)) is not None for index in range(4))
    with pytest.raises(RuntimeError, match="interrupted"), database.transaction() as conn:
        conn.execute("DELETE FROM prepared_pushes")
        raise RuntimeError("interrupted")
    record_verification("0", "old-attempt", "failed")
    prepared = load_prepared_push("0")
    assert prepared is not None and prepared.checks_state == ""
    record_verification("0", "0", "passed")
    prepared = load_prepared_push("0")
    assert prepared is not None and prepared.checks_state == "passed"
