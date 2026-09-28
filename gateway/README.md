# OpenSRE Messaging Gateway

Standalone inbound messaging gateway for chat platforms: Telegram DM text chat
via long polling, Slack mentions/DMs via **Socket Mode** (default) or **Events API
HTTP**, Discord via Gateway WebSocket, and Buzz mentions via relay polling.

The gateway is a separate surface. Transports receive an injected turn runner;
agent startup and integration loading run through the shared harness, not
transport-specific code.

## Entry points

| What you want | File / symbol | How it is started |
|---------------|---------------|-------------------|
| **Production entry** | CLI composition root (outside `gateway/`) | `opensre gateway start` / `--foreground` (injects slash ports) |
| **Package main** | `gateway/__main__.py` → `main()` | Fails closed — no slash-port glue |
| **Process composition root** | `gateway/core/lifecycle/controller.py` → `GatewayController` | Injected `slash_ports_factory` from CLI; bare `controller.main` fails closed |
| **Daemon (pidfile + spawn)** | `gateway/core/process/supervision.py` | Used by CLI `gateway start/stop/status` (pidfile + `components.json`); caller passes argv |
| **Web surface (web-only task)** | `gateway/web/webapp.py` → `app` | `uvicorn gateway.web.webapp:app` (`MODE=web` in Docker) |
| **Surface startup** | `gateway/startup.py` → `start_gateway` / `StartedGateway` | Called by `GatewayController.start_surfaces` |
| **Chat transport registry** | `gateway/transports/startup.py` → `TRANSPORTS` / `start_transports` | Used by `start_gateway` |
| **Telegram transport** | `gateway/transports/telegram/startup.py` → `start_telegram_worker` | Via the startup registry |
| **Slack transport** | `gateway/transports/slack/startup.py` → `start_slack_worker` | Via the startup registry |
| **Discord transport** | `gateway/transports/discord/startup.py` → `start_discord_worker` | Via the startup registry (includes readiness wait) |
| **Per-message turn** | `infrastructure/turn_host/turn_runner.py` → `TurnRunner` | Injected into chat transports as the agent callback |

```text
opensre gateway start
        │
        ▼
gateway.core.process.supervision.start_gateway_daemon
        │  spawns surface-owned argv (see surfaces.shared.gateway_entrypoint):
        │    venv:   python -m surfaces.gateway_entry
        │    frozen: opensre gateway start --foreground
        ▼
surfaces/gateway_entry.py  (or Click foreground → same composition root)
        │  injects headless slash ports
        ▼
gateway.core.lifecycle.controller.GatewayController.start_gateway
        ├── start_surfaces()  →  gateway.startup.start_gateway
        │     ├── web/web_server  →  web/webapp:app
        │     └── transports/startup.start_transports
        │           (telegram / slack / discord / buzz startup)
        └── start_scheduler()   # hosts infrastructure.scheduling.scheduler; not a gateway surface
```

Layout: `core/` (runtime, storage, …), `startup.py` (surface composer),
`transports/` (slack, discord, telegram peers), and `web/` (web surface). See
`AGENTS.md`.

## How the pieces fit (surfaces, gateway, integrations)

Three things that are easy to mix up.

A surface is how a person talks to the agent (message in, answer out). Today
there are three: the interactive shell (`surfaces/interactive_shell`), the
CLI one-shot (`surfaces/cli`), and the gateway (`gateway/`, chat apps).

The gateway is the always-on process that connects a chat app to the agent.
It speaks Telegram (long poll), Slack (Socket Mode or Events API HTTP), Discord
(Gateway WebSocket), and Buzz (relay polling). Slack's two inbound modes share
the same turn stack; only how the payload arrives differs
(`gateway/transports/slack/transport/`).

Integrations and tools are the outbound / teammate side: the agent reading
and posting on a platform. Slack's shared client is
`integrations/slack/web_client.py`. Common Slack tools:
`slack_send_message` (webhook), `slack_reply_message` (bot token, any
channel), `slack_read_messages` (history / thread),
`slack_list_team_members` (roster), plus search / join / react helpers
under `integrations/slack/tools/`. See `docs/messaging/slack.mdx` for
OAuth scopes. Telegram has `telegram_send_message`; Discord is gateway
chat plus delivery today (see `docs/messaging/discord.mdx`).

Inbound and outbound are independent per platform:

| | Inbound (person → agent) | Outbound / teammate tools |
|---|---|---|
| **Telegram** | Yes — `gateway/transports/telegram/` | Yes — integration + `telegram_send_message` |
| **Slack** | Yes — `gateway/transports/slack/` (Socket Mode by default; Events API HTTP optional; each thread is a conversation) | Yes — webhook + bot-token tools |
| **Discord** | Yes — `gateway/transports/discord/` (DMs, mentions, threads) | Delivery + slash registration |
| **Buzz** | Yes — `gateway/transports/buzz/` (mentions and channel follow-ups) | Yes — integration + `buzz_send_message` |

**One core for every surface.** Shell, CLI, and the gateway transports all hand the
message to the same place: a session-scoped `HeadlessAgent`
(`agent.handle(...)` via `TurnRunner`). They differ only in *how they
receive input and send output* — never in how the agent thinks.

## Quick start

```bash
# Allow your Telegram user id (from @userinfobot)
uv run opensre messaging allow -p telegram -u 123456789

# Allow your Slack member id (profile → Copy member ID; see below)
uv run opensre messaging allow -p slack -u U0123ABCD

# Start the gateway daemon (web + chat). The process also hosts infrastructure.scheduling.scheduler.
uv run opensre gateway start
```

**Find your Slack user id (member ID):**

1. Open your profile in the Slack app (avatar / name).
2. Click **⋯** (More) next to **View as** / profile actions.
3. Choose **Copy member ID** — that value starts with `U…` and is what
   `SLACK_ALLOWED_USERS` / `messaging allow -p slack -u …` need.
4. Do **not** use `@display-name` (e.g. `@Yauhen`); only the member ID is stable.

Both transports load configuration the same way: tokens from env first with the
integration store as fallback; allowed users from the integration store
(written by `opensre messaging allow`) first with the `*_ALLOWED_USERS` env
var as fallback.

DM your bot from Telegram, mention/DM it in Slack, chat in Discord, or mention
the agent in Buzz (see `docs/messaging/` for app setup). Slack Socket Mode needs
no public URL; Events API HTTP needs a reachable URL and `SLACK_SIGNING_SECRET`.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram user ids |
| `TELEGRAM_GATEWAY_MAX_CONCURRENT` | Parallel turns across chats (defaults to the `OPENSRE_SIZE_PROFILE` limit: SMALL=1, MEDIUM=2, LARGE=4) |
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | Slack app-level token for Socket Mode (`xapp-…`) |
| `SLACK_SIGNING_SECRET` | Slack signing secret for Events API HTTP request verification |
| `SLACK_GATEWAY_INBOUND_TRANSPORT` | `socket_mode` (default) or `events_api_http` |
| `SLACK_GATEWAY_HTTP_PORT` | Port for the Events API HTTP listener (default `3000`) |
| `SLACK_GATEWAY_ALLOW_LOCAL_DEDUP` | `1` allows process-local event dedup for Events API (single replica only) |
| `SLACK_ALLOWED_USERS` | Comma-separated Slack user ids (required unless open workspace) |
| `SLACK_ALLOW_OPEN_WORKSPACE` | `1` allows any workspace member (dogfood only) |
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `DISCORD_ALLOWED_USERS` | Comma-separated Discord user snowflakes |
| `DISCORD_ALLOW_OPEN_GUILD` | `1` allows any guild member (dogfood only) |
| `BUZZ_PRIVATE_KEY` | Nostr private key for the agent identity |
| `BUZZ_RELAY_URL` | Buzz relay URL (default `http://localhost:3000`) |
| `BUZZ_ALLOWED_PUBKEYS` | Comma-separated 64-character hex pubkeys allowed to talk to the agent |
| `BUZZ_GATEWAY_MAX_CONCURRENT` | Parallel Buzz turns (default 4) |

Pairing via `opensre messaging pair` uses the same integration-store policy as the gateway.

## Adding a chat platform

The message handler is **transport-agnostic** — it takes
`(text, session, output, logger)` and knows nothing about any platform. To add a
platform you do **not** touch the agent, prompts, or tools. You add one package
with the same five pieces `gateway/transports/telegram/` and `gateway/transports/slack/` both have:

1. **Settings** (`settings.py`): env-backed config, raising
   `GatewayConfigurationError` (from `gateway/core/lifecycle/errors.py`) when missing.
2. **A listener** (`startup.py` + the transport worker): receives inbound
   messages and calls the shared handler with `(text, session, output, logger)`.
3. **Inbound security**: authorize each message and audit-log it
   (`integrations/messaging_security`).
4. **Turn output** (implement `TurnOutput` from
   `infrastructure/turn_host/turn_output.py`): streams status and delivers the answer.
5. **Session binding** via `gateway/core/storage/session/resolver.py` with a new
   `platform` value: map the platform conversation key to a `Session`.

Then register it in the composition root (`GatewayController` in
`gateway/core/lifecycle/controller.py`) beside the existing transports. Reuse the handler
from `TurnRunner(...)` as-is.

**What you never change:** `TurnRunner`, harness prompts/tools, or the
session agent pool. Keeping the handler transport-agnostic is exactly what makes
a new platform a small, self-contained add.
