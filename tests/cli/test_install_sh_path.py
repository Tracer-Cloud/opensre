"""Tests for the configure_path() function in install.sh."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# install.sh is a POSIX shell script that exercises zsh/bash/fish rc-file
# behaviour, and these tests drive it via ``subprocess.run(["bash", "-c", ...])``.
# On the GitHub Actions ``windows-latest`` runner, ``bash`` is resolved to
# ``wsl.exe`` and the runner has no installed WSL distribution — every
# ``_run`` call exits 1 with a "Windows Subsystem for Linux has no installed
# distributions" message and none of the asserted rc files get written.
# Skip the whole module rather than chase a Windows analogue for a Unix-only
# installer script.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "install.sh is POSIX-only; the Windows runner has no usable bash "
        "(resolves to unconfigured WSL), so this module's subprocess-driven "
        "tests cannot run there. See issue #1099."
    ),
)

INSTALL_SH = Path(__file__).parents[2] / "install.sh"
_LOCAL_BIN = ".local/bin"


def _run(
    tmp_path: Path, shell: str, platform: str = "linux", install_dir: str | None = None
) -> subprocess.CompletedProcess[str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    idir = install_dir if install_dir is not None else str(fake_home / _LOCAL_BIN)

    script = textwrap.dedent(f"""\
        __fn=$(awk 'p&&/^}}$/{{print;exit}} /^configure_path\\(\\)/{{p=1}} p{{print}}' {INSTALL_SH})
        if [ -z "$__fn" ]; then
            echo "configure_path not found in install.sh" >&2
            exit 1
        fi
        log()  {{ printf '%s\\n' "$*"; }}
        warn() {{ printf 'Warning: %s\\n' "$*" >&2; }}
        eval "$__fn"
        INSTALL_DIR="{idir}" platform="{platform}" HOME="{fake_home}" SHELL="{shell}" configure_path
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_zsh_writes_export_to_zshrc(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    zshrc = tmp_path / "home" / ".zshrc"
    assert zshrc.exists()
    assert _LOCAL_BIN in zshrc.read_text()


def test_bash_linux_writes_to_bashrc(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/bash", platform="linux")
    assert result.returncode == 0, result.stderr
    bashrc = tmp_path / "home" / ".bashrc"
    assert bashrc.exists()
    assert _LOCAL_BIN in bashrc.read_text()


def test_bash_macos_writes_to_bash_profile(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/bash", platform="darwin")
    assert result.returncode == 0, result.stderr
    bash_profile = tmp_path / "home" / ".bash_profile"
    assert bash_profile.exists()
    assert _LOCAL_BIN in bash_profile.read_text()


def test_fish_uses_fish_add_path(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/usr/bin/fish")
    assert result.returncode == 0, result.stderr
    fish_config = tmp_path / "home" / ".config" / "fish" / "config.fish"
    assert fish_config.exists()
    assert "fish_add_path" in fish_config.read_text()


def test_unknown_shell_prints_manual_instructions(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/dash")
    assert result.returncode == 0, result.stderr
    home = tmp_path / "home"
    assert not (home / ".zshrc").exists()
    assert not (home / ".bashrc").exists()
    assert not (home / ".bash_profile").exists()
    assert "export PATH" in result.stdout or "export PATH" in result.stderr


def test_idempotent_no_duplicate_on_rerun(tmp_path: Path) -> None:
    _run(tmp_path, shell="/bin/zsh")
    _run(tmp_path, shell="/bin/zsh")
    content = (tmp_path / "home" / ".zshrc").read_text()
    export_lines = [ln for ln in content.splitlines() if _LOCAL_BIN in ln and "export PATH" in ln]
    assert len(export_lines) == 1


def test_skips_when_install_dir_already_in_rc(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    idir = str(home / _LOCAL_BIN)
    zshrc = home / ".zshrc"
    zshrc.write_text(f'export PATH="$PATH:{idir}"\n')
    original = zshrc.read_text()

    result = _run(tmp_path, shell="/bin/zsh", install_dir=idir)
    assert result.returncode == 0, result.stderr
    assert zshrc.read_text() == original


def test_creates_rc_file_when_missing(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "home" / ".zshrc").exists()


def test_marker_comment_present(tmp_path: Path) -> None:
    _run(tmp_path, shell="/bin/zsh")
    content = (tmp_path / "home" / ".zshrc").read_text()
    assert "# Added by opensre installer" in content


def test_post_install_message_mentions_source(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "source" in combined


def test_fish_creates_parent_dirs(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/usr/bin/fish")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "home" / ".config" / "fish" / "config.fish").exists()


def test_readds_export_when_marker_present_but_line_removed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    zshrc = home / ".zshrc"
    zshrc.write_text("# Added by opensre installer\n")

    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    content = zshrc.read_text()
    assert _LOCAL_BIN in content


# ---------------------------------------------------------------------------
# Helpers and tests for the post-install onboarding hint (issue #1153)
# ---------------------------------------------------------------------------


def _find_post_install_start_line() -> int:
    """Return the line number where the post-install output block starts in install.sh.

    We look for the first line of the version-print block that immediately
    follows the ``install_binary`` call — i.e. the ``if [ "$INSTALL_CHANNEL"``
    line that opens the "Installed opensre ..." log statement.  Everything from
    that line to EOF is the post-install output block that we want to run in
    tests.
    """
    marker = 'if [ "$INSTALL_CHANNEL" = "main" ]; then'
    lines = INSTALL_SH.read_text().splitlines()
    # Walk backwards from EOF so we pick up the last (main-script-level)
    # occurrence, not any occurrence inside a function body.
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == marker:
            return i + 1  # 1-indexed for tail / awk
    raise RuntimeError(
        f"Could not locate post-install block in {INSTALL_SH}. Did the script structure change?"
    )


def _run_post_install(
    tmp_path: Path,
    shell: str,
    platform: str = "linux",
    install_channel: str = "release",
    installed_version: str = "2026.4.1",
    dir_already_on_path: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the real post-install output block of install.sh with side-effects stubbed.

    Unlike ``_run()``, which only calls ``configure_path()`` in isolation, this
    helper sources *the actual lines* that sit at the bottom of install.sh
    (version print + configure_path + onboarding hint) rather than copying
    them into the test.  That means if the hint is removed from install.sh
    the assertions will correctly fail — there is no tautology.

    The approach:
      1. Load all function definitions from install.sh via awk.
      2. Stub the four side-effect functions so no network/binary calls occur.
      3. Set every shell variable the output block needs.
      4. Use ``tail -n +N`` to feed the real post-install lines from install.sh
         to bash, so the test drives install.sh source directly.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    idir = str(fake_home / _LOCAL_BIN)

    # When dir_already_on_path=True, configure_path() hits the early return
    # and prints nothing.  The onboarding hint must still appear.
    path_value = f"{idir}:/usr/bin:/bin" if dir_already_on_path else "/usr/bin:/bin"

    start_line = _find_post_install_start_line()

    script = textwrap.dedent(f"""\
        # 1. Load every function definition from install.sh
        eval "$(awk '
            /^[a-z_][a-z_]*\\(\\)/ {{ in_fn=1 }}
            in_fn {{ print }}
            in_fn && /^\\}}$/ {{ in_fn=0 }}
        ' {INSTALL_SH})"

        # 2. Stub side-effect functions — no binary or network calls
        install_binary()               {{ :; }}
        get_binary_path_from_archive() {{ printf '/tmp/fake-opensre\\n'; }}
        verify_binary_version()        {{ printf '%s\\n' "${{2:-{installed_version}}}"; }}
        run_with_privilege()           {{ "$@"; }}

        # 3. Set every variable the output block reads
        BIN_NAME="opensre"
        INSTALL_DIR="{idir}"
        INSTALL_CHANNEL="{install_channel}"
        installed_version="{installed_version}"
        platform="{platform}"
        HOME="{fake_home}"
        SHELL="{shell}"
        PATH="{path_value}"
        export HOME SHELL PATH

        # 4. Execute the real post-install lines sourced directly from install.sh.
        #    tail -n +{start_line} feeds everything from the version-print block
        #    to EOF, so any change to those lines in install.sh is immediately
        #    reflected here — no copy-paste tautology.
        eval "$(tail -n +{start_line} {INSTALL_SH})"
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_install_sh_contains_onboarding_hint() -> None:
    """Contract test: the hint string must be present in install.sh source.

    This is a direct grep of the script file — independent of any subprocess
    execution — so it will fail immediately if the hint is removed from
    install.sh even if the subprocess-based tests are somehow still passing.
    """
    source = INSTALL_SH.read_text()
    assert "opensre onboard" in source, (
        "install.sh does not contain the onboarding hint. "
        "Expected a line like: log \"Next: run 'opensre onboard' to complete setup.\""
    )


def test_install_ps1_contains_onboarding_hint() -> None:
    """Contract test: the hint string must be present in install.ps1 source."""
    install_ps1 = Path(__file__).parents[2] / "install.ps1"
    source = install_ps1.read_text()
    assert "opensre onboard" in source, (
        "install.ps1 does not contain the onboarding hint. "
        "Expected a line like: Write-Host \"Next: run 'opensre onboard' to complete setup.\""
    )


def test_onboarding_hint_shown_when_path_not_set(tmp_path: Path) -> None:
    """Hint appears on a first install where configure_path writes the rc file."""
    result = _run_post_install(tmp_path, shell="/bin/zsh", dir_already_on_path=False)
    assert result.returncode == 0, result.stderr
    assert "opensre onboard" in result.stdout + result.stderr


def test_onboarding_hint_shown_when_path_already_set(tmp_path: Path) -> None:
    """Hint appears even when configure_path returns early (install dir already on PATH).

    This is the silent-upgrade scenario that the old configure_path-only
    helper could never cover: configure_path() hits the early return at
    line 490 and outputs nothing, yet the user must still see the hint.
    """
    result = _run_post_install(tmp_path, shell="/bin/zsh", dir_already_on_path=True)
    assert result.returncode == 0, result.stderr
    assert "opensre onboard" in result.stdout + result.stderr


def test_onboarding_hint_shown_for_bash_linux(tmp_path: Path) -> None:
    """Hint appears on bash/linux installs."""
    result = _run_post_install(tmp_path, shell="/bin/bash", platform="linux")
    assert result.returncode == 0, result.stderr
    assert "opensre onboard" in result.stdout + result.stderr


def test_onboarding_hint_shown_for_main_channel(tmp_path: Path) -> None:
    """Hint appears when installing the rolling main build (not a versioned release)."""
    result = _run_post_install(
        tmp_path,
        shell="/bin/zsh",
        install_channel="main",
        installed_version="main",
    )
    assert result.returncode == 0, result.stderr
    assert "opensre onboard" in result.stdout + result.stderr


def test_onboarding_hint_appears_after_version_line(tmp_path: Path) -> None:
    """The onboarding hint must appear AFTER the 'Installed opensre v...' line."""
    result = _run_post_install(tmp_path, shell="/bin/zsh", installed_version="2026.4.1")
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    installed_pos = output.find("Installed opensre")
    onboard_pos = output.find("opensre onboard")
    assert installed_pos != -1, "'Installed opensre' line missing from output"
    assert onboard_pos != -1, "'opensre onboard' hint missing from output"
    assert onboard_pos > installed_pos, (
        "Onboarding hint must come after the install confirmation line"
    )
