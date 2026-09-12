"""Atomic, domain-owned repair state and active-run identity."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from filelock import FileLock

from config.constants.ci_repair import CI_REPAIR_DIRECTORY
from config.constants.paths import OPENSRE_HOME_DIR
from integrations.github.tools.ci_repair_loop.models import RepairRun


class RepairStore:
    """Serialize reservations and snapshots; interrupted writes preserve the old state."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or OPENSRE_HOME_DIR / CI_REPAIR_DIRECTORY
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "runs.json"
        self.lock = FileLock(str(self.path) + ".lock", timeout=5)

    def _read(self) -> dict[str, RepairRun]:
        if not self.path.exists():
            return {}
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if document.get("version") != 1:
            raise ValueError("Unsupported CI repair store version.")
        return {key: RepairRun.model_validate(value) for key, value in document["runs"].items()}

    def _write(self, runs: dict[str, RepairRun]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.root, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(
                    {"version": 1, "runs": {key: run.model_dump() for key, run in runs.items()}},
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def reserve(self, candidate: RepairRun) -> tuple[RepairRun, bool]:
        """Return an active run for this scope without extending its deadline."""
        with self.lock:
            runs = self._read()
            for run in runs.values():
                if run.identity[1:] == candidate.identity[1:] and not run.terminal:
                    if run.actor.casefold() != candidate.actor.casefold():
                        raise ValueError(
                            "Another GitHub account already has an active repair for this target."
                        )
                    return run, True
            runs[candidate.id] = candidate
            self._write(runs)
            return candidate, False

    def get(self, run_id: str) -> RepairRun:
        self.directory(run_id)
        with self.lock:
            run = self._read().get(run_id)
        if run is None:
            raise ValueError("Unknown CI repair run.")
        return run

    def save(self, run: RepairRun) -> None:
        with self.lock:
            runs = self._read()
            previous = runs.get(run.id)
            if previous is not None and previous.terminal and not run.terminal:
                raise ValueError("A completed CI repair cannot become active again.")
            runs[run.id] = run
            self._write(runs)

    def directory(self, run_id: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{12}", run_id) is None:
            raise ValueError("Invalid CI repair run id.")
        return self.root / run_id
