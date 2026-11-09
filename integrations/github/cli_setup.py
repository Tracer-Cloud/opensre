"""Hand GitHub setup to the OpenSRE app without collecting credentials."""

from __future__ import annotations

from config.account import load_account_record
from infrastructure.terminal.browser import open_in_browser
from integrations.github.app_connection import github_setup_url
from integrations.setup.result import SetupPending


def setup_github() -> SetupPending:
    """Open or print the app setup page and leave setup pending."""
    url = github_setup_url()
    if open_in_browser(url, target="integration_setup"):
        print(f"Opened {url}")
    else:
        print(f"Open {url} in a browser on any device.")
    print("Connect GitHub there with browser sign-in or the app's token form. Setup is pending.")
    if load_account_record() is None:
        print("Run `opensre account login` to load your app connections on this machine.")
    print("After connecting, return to OpenSRE and refresh your connections.")
    return SetupPending(service="github", setup_url=url)
