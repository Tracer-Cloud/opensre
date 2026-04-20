"""Minimal HTTP server for EC2 hello-world smoke test."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8080


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: dict[str, str] = {"message": "hello world"}
        if self.path == "/ping":
            body = {"pong": "hello world"}

        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002, ARG002
        print(f"[hello-world] {args[0]} {args[1]} {args[2]}")


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Serving on 0.0.0.0:{PORT}")
    server.serve_forever()
