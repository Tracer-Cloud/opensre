"""Generate the standalone PowerShell installer's shared lifecycle constants."""

from __future__ import annotations

from pathlib import Path

from config.constants import (
    OPENSRE_INSTALL_REPLACE_EXISTING_BINARY_ENV,
    OPENSRE_UPDATE_PARENT_STARTED_ENV,
    WINDOWS_APP_DIR_NAME,
    WINDOWS_CURRENT_POINTER_FILENAME,
    WINDOWS_INSTALL_LOCK_FILENAME,
    WINDOWS_LAUNCHER_MARKER,
    WINDOWS_LAYOUT_MARKER_FILENAME,
    WINDOWS_LAYOUT_MARKER_TEXT,
    WINDOWS_MAX_COMMAND_PATH_LENGTH,
)

_BEGIN = "# BEGIN GENERATED LIFECYCLE CONSTANTS"
_END = "# END GENERATED LIFECYCLE CONSTANTS"


def render_installer_constants() -> str:
    """Render PowerShell assignments from the canonical Python constants."""
    bindings: tuple[tuple[str, str | int], ...] = (
        ("OpenSreLauncherMarker", WINDOWS_LAUNCHER_MARKER),
        ("OpenSreLayoutMarkerName", WINDOWS_LAYOUT_MARKER_FILENAME),
        ("OpenSreLayoutMarkerText", WINDOWS_LAYOUT_MARKER_TEXT),
        ("OpenSreLayoutRootName", WINDOWS_APP_DIR_NAME),
        ("OpenSreCurrentPointerName", WINDOWS_CURRENT_POINTER_FILENAME),
        ("OpenSreInstallLockName", WINDOWS_INSTALL_LOCK_FILENAME),
        ("OpenSreReplaceExistingBinaryEnv", OPENSRE_INSTALL_REPLACE_EXISTING_BINARY_ENV),
        ("OpenSreUpdateParentStartedEnv", OPENSRE_UPDATE_PARENT_STARTED_ENV),
        ("OpenSreMaxCommandPathLength", WINDOWS_MAX_COMMAND_PATH_LENGTH),
    )
    lines = []
    for name, value in bindings:
        if isinstance(value, str):
            escaped = value.replace("`", "``").replace("$", "`$").replace('"', '`"')
            literal = f'"{escaped}"'
        else:
            literal = str(value)
        lines.append(f"$script:{name} = {literal}")
    return "\n".join(lines)


def main() -> None:
    """Refresh the checked-in assignments without adding installer dependencies."""
    installer = Path(__file__).resolve().parents[3] / "install.ps1"
    source = installer.read_text(encoding="utf-8")
    start = source.index(_BEGIN) + len(_BEGIN)
    end = source.index(_END, start)
    updated = source[:start] + "\n" + render_installer_constants() + "\n" + source[end:]
    installer.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
