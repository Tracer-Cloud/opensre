"""Single-attempt loopback OAuth callback listener."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from config.constants.chatgpt import CHATGPT_CALLBACK_PATH
from core.llm.subscriptions.types import SubscriptionLoginRequired


class OAuthCallback:
    """Bind before opening the browser; accept only the pending state and path."""

    def __init__(self, state: str) -> None:
        self.result: dict[str, str] | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *args: object) -> None:
                """Never log authorization codes or callback URLs."""

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(5)

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                values = parse_qs(parsed.query, keep_blank_values=True)
                valid = (
                    parsed.path == CHATGPT_CALLBACK_PATH
                    and len(values.get("state", [])) == 1
                    and secrets.compare_digest(values["state"][0], state)
                    and all(len(value) == 1 for value in values.values())
                )
                self.send_response(HTTPStatus.OK if valid else HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"Return to OpenSRE." if valid else b"Invalid login callback.")
                if valid:
                    owner.result = {key: value[0] for key, value in values.items()}

        try:
            self.server = HTTPServer(("127.0.0.1", 1455), Handler)
        except OSError:
            self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.timeout = 0.25
        self.redirect_uri = f"http://127.0.0.1:{self.server.server_port}{CHATGPT_CALLBACK_PATH}"

    def receive(
        self,
        authorize: Callable[[str], str],
        present: Callable[[str], None],
        *,
        timeout: float = 300,
    ) -> dict[str, str]:
        """Present authorization and wait up to five minutes, without background stdin."""
        try:
            present(authorize(self.redirect_uri))
            deadline = time.monotonic() + timeout
            while self.result is None and time.monotonic() < deadline:
                self.server.handle_request()
            if self.result is None:
                raise SubscriptionLoginRequired("ChatGPT login timed out. Run login again.")
            if self.result.get("error"):
                raise SubscriptionLoginRequired(
                    "ChatGPT authorization was declined. Run login again."
                )
            return self.result
        finally:
            self.server.server_close()
