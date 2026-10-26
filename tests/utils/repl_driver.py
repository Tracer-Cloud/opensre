"""ReplDriver: pty-based driver for live interactive REPL testing.

Fakes a TTY so opensre's isatty() check passes, then sends commands and
captures rendered output. Use this for any test that needs to verify
interactive REPL behavior — slash commands, session management, live
display output — that unit tests with mocked consoles cannot cover.

Usage (context manager, recommended)::

    from tests.utils.repl_driver import ReplDriver

    with ReplDriver() as repl:
        repl.send("/sessions", wait=3.0)
        repl.send("/resume abc1234", wait=3.0)
        assert repl.contains("resumed session")
        assert repl.contains("conversation context loaded")

Usage (manual)::

    repl = ReplDriver()
    repl.start(startup_wait=6.0)
    repl.send("/status", wait=2.0)
    output = repl.text          # ANSI-stripped full output so far
    repl.close()

Design notes:
- os.pty creates a master/slave pair; slave is given to opensre as its
  stdin/stdout/stderr so prompt_toolkit sees a real TTY.
- select() drains output non-blockingly so we never block forever.
- ANSI escape codes are stripped lazily via the `text` property from
  self._raw, so assertions always work on plain text.
- .env is loaded from the repo root so live LLM providers work.
- startup_wait=6.0 covers banner rendering + async event-loop startup.
  Increase it if tests are flaky on slow CI machines.

When NOT to use this:
- Unit tests that mock the console — keep those in tests/cli/.
- Tests that only need SessionState / Session — use tmp_path fixtures.
- Tests that need a real LLM response — use make test-rca instead;
  LLM latency makes pty timing unreliable.
"""

from __future__ import annotations

import contextlib
import os
import pty
import re
import select
import subprocess
import time
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from config.constants.paths import PROJECT_ROOT, REPO_ROOT

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _load_env(*, home: str | None = None) -> dict[str, str]:
    """Merge .env into a copy of the current environment."""
    env = dict(os.environ)
    if home is not None:
        env["HOME"] = home
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        env.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    return env


class ReplDriver:
    """Context-manager driver for a live opensre REPL process."""

    def __init__(
        self,
        *,
        startup_wait: float = 6.0,
        cwd: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self._startup_wait = startup_wait
        self._cwd = str(cwd or REPO_ROOT)
        self._home = str(home) if home is not None else None
        self._master: int | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._raw: bytes = b""

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self, startup_wait: float | None = None) -> None:
        """Start the REPL process and wait for the banner to render."""
        master, slave = pty.openpty()
        self._master = master
        try:
            self._proc = subprocess.Popen(
                ["uv", "run", "opensre"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=_load_env(home=self._home),
                cwd=self._cwd,
            )
        finally:
            os.close(slave)
        self._drain(startup_wait if startup_wait is not None else self._startup_wait)

    def close(self, exit_wait: float = 3.0) -> None:
        """Send /exit and wait for the process to finish."""
        if self._master is not None and self._proc is not None:
            with contextlib.suppress(OSError):
                os.write(self._master, b"/exit\n")
                self._drain(exit_wait)
        if self._proc is not None:
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        if self._master is not None:
            with contextlib.suppress(OSError):
                os.close(self._master)
        self._master = None
        self._proc = None

    def __enter__(self) -> ReplDriver:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ── interaction ───────────────────────────────────────────────────────────

    def send(self, command: str, *, wait: float = 2.0) -> None:
        """Type a command (newline appended) and wait for output to settle."""
        if self._master is None:
            raise RuntimeError("ReplDriver not started — call start() or use as context manager")
        os.write(self._master, (command + "\n").encode())
        self._drain(wait)

    # ── output access ─────────────────────────────────────────────────────────

    @property
    def text(self) -> str:
        """Full captured output with ANSI escape codes stripped."""
        return _ANSI_ESCAPE.sub("", self._raw.decode("utf-8", errors="replace"))

    def contains(self, substring: str) -> bool:
        """Return True if substring appears anywhere in the stripped output."""
        return substring in self.text

    def wait_until_contains(self, *substrings: str, timeout: float = 30.0) -> bool:
        """Drain output until any substring appears or the timeout expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(substring in self.text for substring in substrings):
                return True
            self._drain(min(0.5, deadline - time.monotonic()))
        return any(substring in self.text for substring in substrings)

    def lines(self) -> list[str]:
        """Non-empty visible lines from the stripped output."""
        return [line for line in self.text.splitlines() if line.strip()]

    def reset_output(self) -> None:
        """Clear captured output (useful between phases of a multi-step test)."""
        self._raw = b""

    # ── internals ─────────────────────────────────────────────────────────────

    def _drain(self, timeout: float) -> None:
        """Read all available output from the pty master until timeout."""
        if self._master is None:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                r, _, _ = select.select([self._master], [], [], min(remaining, 0.2))
            except (ValueError, OSError):
                break
            if r:
                try:
                    chunk = os.read(self._master, 8192)
                    if not chunk:
                        break
                    self._raw += chunk
                except OSError:
                    break
