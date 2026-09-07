"""Factory-style first run: webapp account sign-in, then the hosted shell."""

from __future__ import annotations

from rich.markup import escape

from infrastructure.terminal.theme import ERROR, GLYPH_ERROR, GLYPH_SUCCESS
from surfaces.cli.account_auth import AccountAuthError, login_account
from surfaces.cli.account_ui import AccountLoginPresenter
from surfaces.cli.wizard.components import Choice, choose, console, step_header
from surfaces.cli.wizard.summaries import render_factory_setup_header
from surfaces.shared.account_session import account_status

FACTORY_SETUP_TOTAL_STEPS = 2


def _run_account_signup_step(*, step: int, total_steps: int) -> bool:
    """Establish a validated webapp account; return false when cancelled."""
    step_header(step, total_steps, "OpenSRE account")
    current = account_status()
    if current.authenticated and current.record is not None:
        console.print(
            f"[bold]{GLYPH_SUCCESS} Signed in as @{escape(current.record.github_username)}.[/]"
        )
        console.print(f"Hosted model: [bold]{escape(current.record.llm_model)}[/]")
        return True

    presenter = AccountLoginPresenter(console)
    while True:
        try:
            result = login_account(progress=presenter)
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


def run_factory_setup(_argv: list[str] | None = None) -> int:
    """Sign in to the account; callers launch the shell only on success."""
    render_factory_setup_header()
    if not _run_account_signup_step(step=1, total_steps=FACTORY_SETUP_TOTAL_STEPS):
        return 1
    return 0
