"""Atomic, domain-owned repair state and active-run identity."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from filelock import FileLock, Timeout

from config.constants.ci_repair import CI_REPAIR_DIRECTORY
from config.constants.paths import OPENSRE_HOME_DIR
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus

CHECKOUT_REMOVED = "Checkout removed; report and attempt records retained."
CHECKOUT_RETAINED = "Checkout could not be removed; report and attempt records retained."


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

    def reserve(
        self, candidate: RepairRun, *, refusal: Exception | None = None
    ) -> tuple[RepairRun, bool]:
        """Return an active run for this scope without extending its deadline.

        ``refusal`` is raised instead of reserving ``candidate`` when no active
        run covers its scope, so a refused target is never written.
        """
        with self.lock:
            runs = self._read()
            for run in runs.values():
                if run.identity[1:] == candidate.identity[1:] and not run.terminal:
                    if run.deadline <= time.time():
                        self._expire(run, runs)
                        continue
                    if not run.actor_id:
                        raise ValueError(
                            "A legacy repair is still active; retry after its original deadline."
                        )
                    if run.actor_id != candidate.actor_id:
                        raise ValueError(
                            "Another GitHub account already has an active repair for this target."
                        )
                    return run, True
            if refusal is not None:
                raise refusal
            runs[candidate.id] = candidate
            self._write(runs)
            return candidate, False

    def _expire(self, run: RepairRun, runs: dict[str, RepairRun]) -> None:
        """Release expired scope only when its supervisor no longer owns execution."""
        try:
            with FileLock(str(self.directory(run.id)) + ".execution.lock", timeout=0):
                run.status = RepairStatus.TIMED_OUT
                run.finished_at = time.time()
                run.reason = "The previous repair reached its time budget."
                self._write(runs)
        except Timeout as exc:
            raise ValueError("The previous repair is stopping; try again shortly.") from exc

    def mark_registered(self, run_id: str) -> RepairRun:
        """Publish registration without overwriting a worker that has already started."""
        with self.lock:
            runs = self._read()
            run = runs[run_id]
            run.registered = True
            self._write(runs)
            return run

    def get(self, run_id: str) -> RepairRun:
        self.directory(run_id)
        with self.lock:
            run = self._read().get(run_id)
        if run is None:
            raise ValueError("Unknown CI repair run.")
        return run

    def newest_for(self, actor_id: int) -> RepairRun | None:
        """The most recently started run of one GitHub account, or ``None`` when it has none."""
        with self.lock:
            runs = [run for run in self._read().values() if run.actor_id == actor_id]
        if not runs:
            return None
        return max(runs, key=lambda run: run.started_at)

    def save(self, run: RepairRun) -> None:
        with self.lock:
            runs = self._read()
            previous = runs.get(run.id)
            if previous is not None and previous.terminal and not run.terminal:
                raise ValueError("A completed CI repair cannot become active again.")
            runs[run.id] = run
            self._write(runs)

    def discard_checkout(self, run: RepairRun) -> None:
        """Remove a finished run's checkout; the report and attempt records stay.

        A checkout of a real repository is hundreds of megabytes on the shared
        volume and nothing reads it once the run is terminal. The recorded
        cleanup says what actually happened to it.
        """
        if not run.workspace:
            return
        workspace = Path(run.workspace)
        shutil.rmtree(workspace, ignore_errors=True)
        if run.demo:
            # A demo run already describes its own cleanup of the demo resources.
            return
        run.cleanup = CHECKOUT_RETAINED if workspace.exists() else CHECKOUT_REMOVED

    def directory(self, run_id: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{12}", run_id) is None:
            raise ValueError("Invalid CI repair run id.")
        return self.root / run_id
