"""Factory-style first run: webapp account sign-in, then the hosted shell."""

from __future__ import annotations

from rich.markup import escape

from infrastructure.terminal.theme import ERROR, GLYPH_ERROR, GLYPH_SUCCESS, SECONDARY
from surfaces.cli.account_auth import AccountAuthError, login_account
from surfaces.cli.account_ui import AccountLoginPresenter
from surfaces.cli.wizard.components import Choice, choose, console, step_header
from surfaces.cli.wizard.summaries import render_factory_setup_header
from surfaces.shared.account_session import account_status

FACTORY_SETUP_TOTAL_STEPS = 2


def _run_account_signup_step(
    *,
    step: int,
    total_steps: int,
    app_url: str | None = None,
) -> bool:
    """Establish a validated webapp account; return false when cancelled."""
    step_header(step, total_steps, "OpenSRE account")
    current = account_status(app_url=app_url)
    if current.authenticated and current.record is not None:
        identity = current.record.email or current.record.user_id
        console.print(f"[bold]{GLYPH_SUCCESS} Signed in as {escape(identity)}.[/]")
        console.print(f"Hosted model: [bold]{escape(current.record.llm_model)}[/]")
        return True

    presenter = AccountLoginPresenter(console)
    while True:
        try:
            result = login_account(app_url=app_url, progress=presenter)
        except (EOFError, KeyboardInterrupt):
            console.print()
            console.print(f"[{ERROR}]  {GLYPH_ERROR}  Setup cancelled.[/]")
            return False
        except AccountAuthError as exc:
            console.print(
                f"[{ERROR}]  {GLYPH_ERROR}  OpenSRE sign-in failed: {escape(str(exc))}[/]"
            )
            action = choose(
                "OpenSRE sign-in failed. What next?",
                [
                    Choice(value="retry", label="Try again", hint=None),
                    Choice(value="cancel", label="Stay signed out and exit", hint=None),
                ],
                default="retry",
            )
            if action == "cancel":
                return False
            continue

        presenter.success(result)
        return True


def _announce_shell_start(*, step: int, total_steps: int, launches_shell: bool) -> None:
    """Close the setup with its second step, so the header's count is honest.

    The caller opens the shell when ``launches_shell`` is set; otherwise the
    user does, and the command is named here instead of assumed.
    """
    step_header(step, total_steps, "Start the shell")
    if launches_shell:
        console.print(f"  [{SECONDARY}]Opening the shell with your hosted model…[/]")
        return
    console.print(
        f"  [{SECONDARY}]Run[/] [bold]opensre[/bold] "
        f"[{SECONDARY}]to open the shell with your hosted model.[/]"
    )


def run_factory_setup(
    _argv: list[str] | None = None,
    *,
    app_url: str | None = None,
    launches_shell: bool = True,
) -> int:
    """Sign in to the account; callers launch the shell only on success.

    ``launches_shell`` says whether the caller opens the shell on success, which
    decides how the closing step reads.
    """
    render_factory_setup_header()
    if not _run_account_signup_step(
        step=1,
        total_steps=FACTORY_SETUP_TOTAL_STEPS,
        app_url=app_url,
    ):
        return 1
    _announce_shell_start(
        step=FACTORY_SETUP_TOTAL_STEPS,
        total_steps=FACTORY_SETUP_TOTAL_STEPS,
        launches_shell=launches_shell,
    )
    return 0
