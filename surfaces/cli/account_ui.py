"""Rich login/status screens for the GitHub-backed OpenSRE account command."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import questionary
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from config.account import AccountRecord
from config.constants.paths import OPENSRE_HOME_DIR
from infrastructure.terminal.prompt_support import (
    QUESTIONARY_QMARK,
    questionary_prompt_style,
)
from infrastructure.terminal.theme import (
    BRAND,
    DIM,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    GLYPH_WARNING,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)
from surfaces.cli.account_auth import AccountLoginResult, AccountLogoutResult
from surfaces.shared.account_session import AccountStatus

_console = Console(
    highlight=False, force_terminal=True, color_system="truecolor", legacy_windows=False
)
_KEY_COL = 14


def _display_home() -> str:
    path = Path(OPENSRE_HOME_DIR).expanduser()
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def _print_kv(console: Console, key: str, value: str, value_style: str = TEXT) -> None:
    row = Text()
    row.append(f"    {key:<{_KEY_COL}}", style=SECONDARY)
    row.append(value, style=value_style)
    console.print(row)


def _print_url(console: Console, url: str) -> None:
    line = Text("     ")
    link = Text(url, style=f"bold {HIGHLIGHT}")
    link.stylize(f"link {url}")
    line.append_text(link)
    console.print(line)


def _print_warning_banner(console: Console, title: str) -> None:
    console.print()
    console.print(Rule(style=DIM))
    line = Text()
    line.append(f"  {GLYPH_WARNING}  ", style=f"bold {WARNING}")
    line.append(title, style=f"bold {TEXT}")
    console.print(line)
    console.print(Rule(style=DIM))
    console.print()


def _print_success_banner(console: Console, title: str) -> None:
    console.print()
    console.print(Rule(style=DIM))
    done = Text()
    done.append(f"  {GLYPH_SUCCESS}  ", style=f"bold {HIGHLIGHT}")
    done.append(title, style=f"bold {TEXT}")
    console.print(done)
    console.print(Rule(style=DIM))
    console.print()


class AccountLoginPresenter:
    """Factory-style numbered steps and themed success copy for account login."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or _console

    def prompt_sign_in(self, url: str, *, opened: bool) -> None:
        console = self._console
        console.print()
        console.print("Sign in to OpenSRE with GitHub:")
        console.print()
        if opened:
            console.print("  1. Your browser will open this link")
        else:
            console.print("  1. Open this URL in your browser")
        _print_url(console, url)
        if opened:
            console.print(
                f"     [{SECONDARY}](if it doesn't open, copy that URL into your browser).[/]"
            )
        else:
            console.print(
                f"     [{SECONDARY}](copy the link, then return here once GitHub is connected).[/]"
            )
        console.print("  2. Sign in with GitHub.")
        console.print("  3. Connect repository and security access.")
        console.print()
        console.print(f"  [{SECONDARY}]Waiting for you to approve in the browser…[/]")

    def authorization_received(self) -> None:
        console = self._console
        console.print()
        line = Text()
        line.append(f"  {GLYPH_SUCCESS} ", style=f"bold {HIGHLIGHT}")
        line.append("Browser authorization received.", style=TEXT)
        console.print(line)
        console.print(f"  [{SECONDARY}]Finishing OpenSRE account setup…[/]")

    def setup_complete(self) -> None:
        console = self._console
        github = Text()
        github.append(f"  {GLYPH_SUCCESS} ", style=f"bold {HIGHLIGHT}")
        github.append("GitHub integration connected.", style=TEXT)
        console.print(github)
        hosted = Text()
        hosted.append(f"  {GLYPH_SUCCESS} ", style=f"bold {HIGHLIGHT}")
        hosted.append("Hosted model activated.", style=TEXT)
        console.print(hosted)

    def warn_active_session(self, status: AccountStatus) -> None:
        who = f"@{status.record.github_username}" if status.record else "this account"
        _print_warning_banner(self._console, "A session is already active")
        if status.record is not None:
            _print_account_fields(self._console, status.record)
        self._console.print()
        note = Text()
        note.append("  Signed in as ", style=SECONDARY)
        note.append(who, style=f"bold {TEXT}")
        note.append(". This session is valid. Signing in again replaces it.", style=SECONDARY)
        self._console.print(note)
        self._console.print()

    def confirm_replace(self) -> bool:
        """Ask whether to replace the active session. Default is keep it."""
        if not sys.stdin.isatty():
            return False
        result = questionary.confirm(
            "Replace this session and sign in again?",
            default=False,
            qmark=QUESTIONARY_QMARK,
            style=questionary_prompt_style(),
        ).ask()
        return bool(result)

    def session_kept(self) -> None:
        console = self._console
        line = Text()
        line.append(f"  {GLYPH_SUCCESS}  ", style=f"bold {HIGHLIGHT}")
        line.append("Keeping the current session.", style=TEXT)
        console.print(line)
        console.print(
            f"  [{SECONDARY}]Run[/] [bold]opensre account logout[/bold] "
            f"[{SECONDARY}]to sign out, or[/] [bold]opensre account login --force[/bold] "
            f"[{SECONDARY}]to replace it.[/]"
        )
        console.print()

    def replacing_session(self, status: AccountStatus) -> None:
        who = f"@{status.record.github_username}" if status.record else "this account"
        console = self._console
        console.print()
        line = Text()
        line.append(f"  {GLYPH_WARNING}  ", style=f"bold {WARNING}")
        line.append(f"Replacing the active session for {who}.", style=TEXT)
        console.print(line)

    def success(
        self,
        result: AccountLoginResult,
        *,
        missing_scopes: Sequence[str] = (),
    ) -> None:
        record = result.record
        _print_success_banner(self._console, f"Signed in as @{record.github_username}")
        _print_account_fields(self._console, record)
        if result.warning:
            self._console.print()
            warn = Text()
            warn.append(f"  {GLYPH_WARNING}  ", style=f"bold {WARNING}")
            warn.append(result.warning, style=WARNING)
            self._console.print(warn)
        if missing_scopes:
            self._console.print()
            warn = Text()
            warn.append(f"  {GLYPH_WARNING}  ", style=f"bold {WARNING}")
            warn.append(
                "GitHub integration is missing "
                + ", ".join(missing_scopes)
                + ". Run account login again and approve repository and security access.",
                style=WARNING,
            )
            self._console.print(warn)
        self._console.print()


def _print_account_fields(console: Console, record: AccountRecord) -> None:
    _print_kv(console, "github", f"@{record.github_username}")
    if record.email:
        _print_kv(console, "email", record.email)
    _print_kv(console, "org", record.organization_id)
    _print_kv(
        console,
        "llm",
        f"{record.llm_provider} · {record.llm_model}  (hosted by OpenSRE)",
    )
    _print_kv(console, "expires", record.token_expires_at, DIM)
    _print_kv(console, "store", _display_home(), BRAND)


def render_account_status(status: AccountStatus) -> None:
    """Print local account status in the same theme as login success."""
    console = _console
    if status.authenticated and status.record is not None:
        _print_success_banner(console, f"Signed in as @{status.record.github_username}")
        _print_account_fields(console, status.record)
        _print_kv(console, "detail", status.detail, SECONDARY)
        console.print()
        return

    console.print()
    console.print(Rule(style=DIM))
    title = Text()
    title.append(f"  {GLYPH_ERROR}  ", style=f"bold {ERROR}")
    title.append("Not signed in", style=f"bold {TEXT}")
    console.print(title)
    console.print(Rule(style=DIM))
    console.print()
    if status.record is not None:
        _print_account_fields(console, status.record)
    _print_kv(console, "detail", status.detail, SECONDARY)
    console.print()


def render_account_logout(result: AccountLogoutResult) -> None:
    """Print logout result with the shared success/warning glyphs."""
    console = _console
    console.print()
    line = Text()
    if result.remote_revoked:
        line.append(f"  {GLYPH_SUCCESS}  ", style=f"bold {HIGHLIGHT}")
        line.append(result.detail, style=TEXT)
    else:
        line.append(f"  {GLYPH_WARNING}  ", style=f"bold {WARNING}")
        line.append(result.detail, style=WARNING)
    console.print(line)
    console.print()


__all__ = [
    "AccountLoginPresenter",
    "render_account_logout",
    "render_account_status",
]
