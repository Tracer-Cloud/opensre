"""Run the OpenSRE quickstart wizard."""

from app.cli.prompt_support import install_questionary_escape_cancel
from app.cli.wizard.flow import run_wizard

if __name__ == "__main__":
    install_questionary_escape_cancel()
    raise SystemExit(run_wizard())
