"""Pre-install marker observations retained for later runtime telemetry."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from config.constants.analytics import ANALYTICS_INSTALL_MARKER_STATE_ENV

_STATE_FILENAME = "install_marker_state"
_STATES = frozenset({"present", "absent", "unknown"})


def snapshot_install_marker(config_dir: Path) -> str:
    """Observe the marker without creating any installation state."""
    try:
        (config_dir / "installed").stat()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unknown"
    return "present"


def read_install_marker_state(config_dir: Path) -> str | None:
    """Read the most recent recorded install's observation, never infer first-ever use."""
    if (supplied := os.environ.get(ANALYTICS_INSTALL_MARKER_STATE_ENV)) is not None:
        return supplied if supplied in _STATES else "unknown"
    try:
        state = (config_dir / _STATE_FILENAME).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        return "unknown"
    return state if state in _STATES else "unknown"


def record_install_marker_state(config_dir: Path) -> None:
    """Persist an installer-supplied snapshot atomically without blocking installation."""
    raw = os.environ.get(ANALYTICS_INSTALL_MARKER_STATE_ENV)
    if raw is None:
        return
    state = raw if raw in _STATES else "unknown"
    temporary: Path | None = None
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config_dir,
            prefix=".install_marker_state-",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(f"{state}\n")
        temporary.replace(config_dir / _STATE_FILENAME)
    except OSError:
        pass
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    from config.constants.paths import get_store_path

    print(snapshot_install_marker(get_store_path().parent))
