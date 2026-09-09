# Product analytics contract

The metric inventory and calculation definitions are in [METRICS.md](METRICS.md).

OpenSRE emits product events through one best-effort, non-blocking queue. The
queue sends one versioned event per request to:

```text
POST {OpenSRE app origin}/api/analytics/events
```

The app origin resolves in this order:

1. `OPENSRE_WEBAPP_URL` for a hosted organization silo.
2. The app URL saved with a personal CLI login.
3. `OPENSRE_APP_URL`, then `https://app.opensre.com`.

An invalid explicit URL, an unreadable saved account, a silo URL without
`AGENT_USAGE_SECRET`, or an environment account token that conflicts with the
saved login while local credential storage is enabled disables remote delivery
for that process instead of falling through to another origin or identity.
Environment-only account delivery requires `OPENSRE_APP_URL` and uses that
explicit origin rather than independently persisted account metadata.

The request carries the personal `osre_pat_...` bearer for a signed-in CLI or
`AGENT_USAGE_SECRET` for a silo. It has no authorization header before login.
The webapp derives personal account and organization identity from the personal
bearer. A silo bearer authenticates the runtime; its configured organization is
a bearer-authenticated assertion, and it does not resolve a Clerk user.
Anonymous requests cannot establish either identity, and `properties.user_id`
is never treated as an OpenSRE account ID.

Authenticated requests are HMAC-SHA256 signed over the timestamp and exact
JSON bytes with the request bearer. The bearer stays in owner-only credential
storage and the PostHog project key stays on the webapp; neither is embedded in
the event body or source distribution. Production origins require HTTPS.

## Ingest payload

```json
{
  "schema_version": 1,
  "event_id": "00ed1192-3433-4e46-856a-9a0992e8b212",
  "occurred_at": "2026-09-08T12:34:56.789+00:00",
  "source": "opensre_runtime",
  "anonymous_id": "4d892bf3-7204-4410-9f03-f84190f8a936",
  "event": "cli_invoked",
  "properties": {
    "entrypoint": "opensre",
    "command_family": "integrations"
  }
}
```

- `event_id` is a UUID for recurring events. `install_detected` uses the stable
  `install_detected:{anonymous_id}` key so the server can deduplicate installs.
- `occurred_at` is assigned when the event enters the local queue, not when the
  network request finishes.
- `anonymous_id` is the random installation ID stored in
  `~/.opensre/anonymous_id`. Keep it on authenticated events to join anonymous
  acquisition activity to later account activity.
- `account_authenticated` is emitted after browser login. The webapp resolves
  the Clerk user from the bearer, stores the installation/user association in
  ClickHouse, and merges the same `anonymous_id` into that user in PostHog.
  Downstream linkage accepts any personal-bearer event so a dropped link event
  does not permanently lose the association.
- A successful ingest should return `202 Accepted`. The server should dedupe on
  `(source, event_id)` and reject unknown schema versions or event names.

The accepted event names are the `Event` enum in `events.py`, plus the internal
identity controls `$identify` and `$groupidentify`. The webapp may translate
those controls into its analytics store instead of storing them as product
activity.

## Common properties

Every product event includes:

| Property | Meaning |
| --- | --- |
| `cli_version`, `python_version` | Client compatibility and release adoption. |
| `os_family`, `os_version` | Coarse platform support. |
| `execution_environment` | `local`, `ci`, `container`, or `ci_container`. |
| `is_ci`, `is_container`, `container_runtime` | Filters for human vs automated usage. |
| `composite_fingerprint` | One-way local fingerprint used only when no account identity exists. |
| `identity_persistence` | Whether the anonymous ID was persisted to disk. |
| `surface` | `cli`, `slack`, `telegram`, `discord`, or `buzz`, when known. |
| `session_id` | OpenSRE session correlation ID, when known. |
| `organization_id` | Server-resolved for personal requests; a bearer-authenticated runtime assertion for silos; untrusted on anonymous requests. |
| `user_id` | Chat-platform actor ID for gateway turns, not an OpenSRE account ID. |

`$groups`, `$process_person_profile`, `$lib`, and `distinct_id` are retained for
downstream PostHog compatibility. The first-party account user ID belongs in a
server-owned column resolved from the bearer token.

## Event inventory

| Area | Events | Important properties / question answered |
| --- | --- | --- |
| Acquisition | `install_detected`, `account_authenticated`, `cli_invoked` | Install source, login conversion, entrypoint, command names, and boolean flags; never raw argument values. |
| Runtime health | `user_id_load_failed`, `sentry_init_skipped` | Identity persistence and telemetry setup failures. |
| Onboarding | `onboard_started`, `onboard_completed`, `onboard_failed` | Funnel conversion, wizard mode, target, provider, and model. |
| Integrations | `integration_setup_started`, `integration_setup_completed`, `integration_verified`, `integration_removed`, `integrations_listed` | Integration adoption and setup/verification conversion by service. |
| Interactive actions | `terminal_actions_planned`, `terminal_actions_executed`, `terminal_turn_summarized` | Planned/executed/success counts, LLM fallback, and session success/fallback buckets. |
| Agent loop | `react_turn_completed` | Phase, iterations, cap hits, stop reason, tool-call count, latency, provider, and model. |
| AI turn | `$ai_generation` | Turn/session IDs, turn kind, model/provider, latency, tokens, integration snapshot, outcome, and error category. It also contains redacted prompt and response text in `$ai_input` and `$ai_output_choices`. |
| Gateway | `gateway_turn_started`, `gateway_turn_completed`, `gateway_turn_failed` | Surface, answer rate, final intent, latency bucket, and exception type. No message body is included. |
| Scheduled work | `scheduled_task_started`, `scheduled_task_completed`, `scheduled_task_failed` | Task kind, provider, status, and task ID. Failed events can contain a capped error string. |
| Updates | `update_started`, `update_completed`, `update_failed` | Check-only vs update, whether a version changed, and failure class. |
| Local-agent safety | `agent_secret_detected`, `agent_killed`, `agent_kill_failed` | Rule names, count, blocked state, agent type, and result; never the detected secret. |
| Suggested loops | `loop_suggestion_prompted`, `loop_suggestion_selected`, `loop_suggestion_skipped` | Picker exposure and selected use case. |
| Onboarding demo | `onboarding_demo_prompted`, `onboarding_demo_selected`, `onboarding_demo_skipped` | Demo exposure, selected option, and whether it was custom. |
| Execution policy | `repl_execution_policy_decision` | Policy stage, outcome, reason, and planned action count. |

## Product metrics

The first dashboard should keep personal-user and gateway-organization grains
separate. Anonymous IDs are a fallback only for pre-login acquisition:

The existing `analytics_core_metrics.daily_active_users` column measures
authenticated webapp activity. It is not the personal product DAU below, which
must be calculated from `analytics_product_events`.

| Metric | Definition |
| --- | --- |
| Install-to-signup conversion | Non-CI installations whose first server-verified account link resolves to a Clerk signup created between install and first authentication, divided by all non-CI installations. |
| Personal activation | Server-resolved users whose linked installation reaches `onboard_completed`, then records a non-error `$ai_generation`. |
| Gateway activation | Authenticated organizations with an answered `gateway_turn_completed`; do not count gateway actor IDs as users. |
| Onboarding conversion | Distinct non-CI installations completed, and distinct installations failed, each divided separately by distinct installations started. |
| Personal DAU / WAU / MAU | Distinct server-resolved users with personal-bearer `cli_invoked` or `$ai_generation` events in the window. |
| Organization DAU / WAU / MAU | Distinct authenticated organizations with gateway activity in the window, reported separately. |
| D1 / D7 / D30 retention | Personally activated users with another qualifying personal event on the target day/window; compute organization retention separately. |
| Answer rate | Completed gateway turns with `answered=true` divided by completed gateway turns. |
| Action success rate | Sum of `executed_success_count` divided by sum of `executed_count`. |
| LLM fallback rate | `terminal_turn_summarized` events with `fallback_to_llm=true` divided by all summarized turns. |
| Agent reliability | Error, cancellation, and iteration-cap `react_turn_completed` events divided by all ReAct turns. |
| Latency | p50/p95 of gateway duration, ReAct duration, and `$ai_latency`, sliced by surface/model/provider. |
| Integration adoption | Distinct authenticated organizations completing or verifying setup by service. Personal events use a server-resolved organization; silo events use a bearer-authenticated runtime assertion. Current connected inventory remains a webapp database fact, not an event-derived fact. |
| Scheduled-work reliability | Completed vs failed scheduled tasks by task kind and provider. |
| Feature adoption | Personal users by CLI/AI feature and organizations by gateway surface, without combining identity grains. |

Exclude `is_ci=true` from human acquisition and retention dashboards, but keep
it available for automation usage reporting.

## Privacy and failure behavior

All delivery honors `OPENSRE_NO_TELEMETRY=1`,
`OPENSRE_ANALYTICS_DISABLED=1`, and `DO_NOT_TRACK=1`. Opting out prevents even
destination credentials from being resolved. Delivery failure never fails the
user command; it is recorded in `~/.opensre/analytics_errors.log`.

The webapp rejects oversized or unknown payloads, rate-limits both source IPs
and analytics identities, deduplicates event IDs, and rejects stale, missing,
or modified signatures for authenticated traffic. Anonymous pre-login events
cannot carry a trustworthy shared secret: anyone who owns a client machine can
change open-source client code. Treat raw anonymous install counts as
directional, use the server-verified linked conversion for decisions, and keep
an upstream WAF/rate limit on the public route for network-layer DDoS defense.

The `$ai_generation` event is the only product event intended to contain user
content. Secret-shaped values are redacted, but arbitrary incident details are
not. Treat that field as confidential, enforce a retention policy server-side,
and keep it out of broad-access product dashboards.
