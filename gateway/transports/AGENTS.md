# gateway/transports/ — chat peer packages

One package per inbound chat platform. Peers: **none imports another**.

In API-framework terms, transports are the ingress adapters: platform inbound that
authorizes, resolves a session, builds turn output, and calls the turn runner. The
turn contract they implement is `infrastructure.turn_host.turn_callback` /
`infrastructure.turn_host.turn_output`; the registry they register with is
`gateway.transports.startup`; the shared per-turn steps they compose are
`gateway.core.middleware`; the facade that starts them is `gateway/startup.py`.

| Package | Start |
|---------|--------|
| `slack/` | `startup.start_slack_worker` |
| `discord/` | `startup.start_discord_worker` |
| `telegram/` | `startup.start_telegram_worker` |
| `buzz/` | `startup.start_buzz_worker` |

The registry and worker start/stop loop live in this package's own
`startup.py` — the one module here allowed to import its peers (their
`startup` submodules only). Web + chat composition stays in
`gateway/startup.py`. `GatewayController` only holds the opaque `StartedGateway`.
Transport-specific work (settings load, Discord readiness wait) stays in each
package's `startup.py`.

Each owns settings, inbound worker, security, turn output, and `startup.py`.
Anything two transports need belongs in `gateway.core` (per-turn steps in
`gateway.core.middleware`; turn output/callback in `infrastructure.turn_host`).
Concern completeness per transport is pinned by
`gateway/tests/test_transport_contract.py` (with its known-gaps ledger).

Peers must not import `gateway.startup` or `gateway.web`.

Peer import DAG pinned by `gateway/tests/test_package_borders.py`.
Discord↔Slack isolation extras:
`gateway/tests/discord/test_transport_borders.py`.
