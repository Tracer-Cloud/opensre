"""Small Streamable HTTP MCP server for validating the OpenSRE MCP gateway.

The mutation is deliberately simulated: ``restart_service`` only increments an
in-memory counter. It is useful for exercising OpenSRE's approval-gated path
without changing a real system.
"""

from __future__ import annotations

import argparse
import hmac
from http import HTTPStatus

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

server = MCPServer(
    "OpenSRE MCP gateway sample",
    description="Local read/write sample for MCP gateway validation",
    log_level="WARNING",
)
_restart_requests: dict[str, int] = {}


class BearerAuthMiddleware:
    """Require one fixed bearer token while preserving the MCP app lifespan."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._authorization = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            if not hmac.compare_digest(headers.get(b"authorization", b""), self._authorization):
                response = PlainTextResponse(
                    "Bearer sample-challenge-secret",
                    status_code=HTTPStatus.UNAUTHORIZED,
                    headers={"WWW-Authenticate": "Bearer sample-challenge-secret"},
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


@server.tool(description="Return the simulated health of a service.", structured_output=True)
def service_status(service: str) -> dict[str, object]:
    """Return a stable status payload without changing state."""
    return {
        "service": service,
        "status": "healthy",
        "restart_requests": _restart_requests.get(service, 0),
    }


@server.tool(description="Simulate requesting a service restart.", structured_output=True)
def restart_service(service: str) -> dict[str, object]:
    """Record a simulated restart request in process memory."""
    count = _restart_requests.get(service, 0) + 1
    _restart_requests[service] = count
    return {"service": service, "accepted": True, "restart_requests": count}


def main() -> None:
    """Run the sample server on a loopback-only Streamable HTTP endpoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--auth-token", default="")
    args = parser.parse_args()
    if args.auth_token:
        app = server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            host="127.0.0.1",
        )
        uvicorn.run(
            BearerAuthMiddleware(app, args.auth_token),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        )
        return
    server.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=args.port,
        streamable_http_path="/mcp",
        json_response=True,
    )


if __name__ == "__main__":
    main()
