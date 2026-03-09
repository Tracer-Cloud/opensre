"""Open the RCA report in the current code editor when running inside one."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPORT_PATH = Path(tempfile.gettempdir()) / "tracer_last_report.md"


def open_in_editor(content: str) -> None:
    """Write the report to a .md file and open it in the current editor.

    Only activates when VSCODE_IPC_HOOK_CLI is set — meaning the process is
    running inside a Cursor or VS Code integrated terminal. No-op in CI,
    production, or plain terminal sessions.
    """
    if not os.environ.get("VSCODE_IPC_HOOK_CLI"):
        return
    REPORT_PATH.write_text(content, encoding="utf-8")
    for cmd in ("cursor", "code"):
        if shutil.which(cmd):
            subprocess.Popen([cmd, "--reuse-window", str(REPORT_PATH)])
            return
