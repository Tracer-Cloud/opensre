# gateway/web/ — web surface

FastAPI app for health probes and alert intake.
Not a chat transport — does not bind a turn runner or turn output.

| May import | Must not |
|------------|----------|
| `gateway.core.*` | `gateway.transports.*`, `gateway.startup` |

Entry: `webapp.py` (`app`) — `uvicorn gateway.web.webapp:app` when `MODE=web`,
or daemon / shell via `web_server.serve_webapp_in_thread`.

Pinned by `gateway/tests/test_package_borders.py`.

## Process boot (`WEB_PROFILE`)

Importing `gateway.web.webapp` (uvicorn or in-process via
`serve_webapp_in_thread`) runs `configure_process(WEB_PROFILE)` — env, Sentry
(`webapp`), and harness adapters. That is intentional so `MODE=web` and
embedded web boot the same way without a CLI/manager boot. Do
**not** add a second harness-registration site in `web/`; adapters stay in
`bootstrap.adapters` only. In a full gateway process, `GATEWAY_PROFILE` already
ran; `WEB_PROFILE` may still run (separate idempotency key) — steps must stay
safe to re-enter, not invent a divergent registry.
