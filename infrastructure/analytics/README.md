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
  "event": "cli_command_opensre_integrations_verify",
  "properties": {
    "entrypoint": "opensre",
    "command_family": "integrations"
  }
}
```

- `event_id` is a UUID for recurring events. `install_detected` uses the stable
  `install_detected:{anonymous_id}` key so the server can deduplicate installs.
  Recovery of an unverified `installed` marker uses
  `install_detected:{anonymous_id}:delivery-v1` and sets
  `install_detection_reason=unverified_marker`. This is a current detection,
  not a reconstruction of the original installation. The separate key preserves
  any earlier event already stored by the server; `METRICS.md` (Installations)
  defines how consumers collapse the two into one installation.
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
  Only that acknowledgement creates a receipt under `install-deliveries-v1/`,
  keyed by a hash of the installation ID and endpoint URL. The older `installed`
  marker is retained for older clients, but cannot establish first-party delivery.

The accepted event names are the `Event` enum in `events.py`, the dynamic
`cli_command_opensre` family, and the internal identity controls `$identify`
and `$groupidentify`. The webapp may translate
those controls into its analytics store instead of storing them as product
activity.

## Common properties

Every product event includes:

| Property | Meaning |
| --- | --- |
| `cli_version`, `python_version` | Client compatibility and release adoption. |
| `os_family`, `os_version` | Coarse platform support. |
| `analytics_properties_version` | Property evidence contract version; currently `2`, independent of envelope schema `1`. |
| `execution_environment` | `local`, `ci`, `container`, `ci_container`, or `unknown`; a detector classification. |
| `is_ci`, `is_container`, `container_runtime` | Recognized runtime signals, not human verification. Unknown measurements are omitted. |
| `ci_detection_status`, `container_detection_status` | `detected`, `not_detected`, or `unknown`. |
| `automation_status`, `execution_origin` | Reported automation or unknown origin; ingestion upgrades authenticated runner evidence to `confirmed`. |
| `distribution` | `source_checkout`, `editable_package`, `installed_package`, `frozen_binary`, or `unknown`, based on the code loaded by this process. |
| `is_test` | Explicit `OPENSRE_IS_TEST=1` (also `true`/`yes`), a detected test runner, or CI. Independent of distribution. |
| `composite_fingerprint` | One-way local fingerprint used only when no account identity exists. |
| `identity_persistence` | Whether the anonymous ID was persisted to disk. |
| `install_marker_state_before_install` | `present`, `absent`, or `unknown` at the start of the most recent recorded installer run. |
| `surface` | `cli`, `slack`, `telegram`, `discord`, or `buzz`, when known. |
| `session_id` | OpenSRE session correlation ID, when known. |
| `organization_id` | Server-resolved for personal requests; a bearer-authenticated runtime assertion for silos; untrusted on anonymous requests. |
| `user_id` | Chat-platform actor ID for gateway turns, not an OpenSRE account ID. |

`$groups`, `$process_person_profile`, `$lib`, and `distinct_id` are retained for
downstream PostHog compatibility. The first-party account user ID belongs in a
server-owned column resolved from the bearer token.

For `cli_invoked`, `interactive_option` is configuration, with its source in
`interactive_option_source`. `stdin_is_tty` and `stdout_is_tty` measure terminal
state. Use `interactive_shell_rendered` for an observed shell launch.

Prompt events include `turn_outcome`, `response_source`, and `llm_attempted`
when known. The event name alone does not establish AI success: static terminal
dispatch and synthetic fallback text are also logged. Missing token usage is
omitted and described by `token_usage_status`. Integration snapshots use
`integration_snapshot_status`; unavailable inventories do not emit empty lists
or zero counts. Action rates without executed actions are omitted.

The shell and PowerShell installers, and `make install`, snapshot `installed`
before installation work begins, resolving its directory the same way as the
runtime's `get_store_path()`: `OPENSRE_WIZARD_STORE_PATH`'s parent when set,
otherwise `OPENSRE_HOME`, otherwise `~/.opensre`. After a successful
install, the record-only path saves that snapshot in `install_marker_state`
beside the marker. Subsequent product events carry it, including when an
existing marker suppresses `install_detected`; reinstalling does not manufacture
another first-install event or a CLI usage event.

`present` is evidence of prior installation state. `absent` means only that no
marker was found: deleting local state can produce the same result as a new
installation. `unknown` means the check could not establish presence or absence.
When no installer snapshot has been recorded, the property is omitted. The
value describes the most recent recorded installer run, not
necessarily the first installation or the current invocation. Do not map
`absent` to “first-ever install.”

## CLI invocation names and distribution

Command names are generated from registered command tokens, with hyphens
normalized to underscores: `opensre health --rate 5` emits
`cli_command_opensre_health`; `opensre integrations verify slack` emits
`cli_command_opensre_integrations_verify`. Bare `opensre` emits
`cli_command_opensre`. These events record invocation, not completion or success.
Arguments and option values never enter the name.

Alternate Python entrypoints use the equivalent OpenSRE command name; the
`entrypoint` property preserves how they were launched. Each invocation emits
one command event, replacing `cli_invoked`. Readers must accept both historical
`cli_invoked` records and the new family. Deploy the webapp's family validation
before distributing a client that emits these names.

`source_checkout` and `editable_package` identify local development code.
`installed_package` and `frozen_binary` identify packaged code, including locally
built packages; they do not establish publisher signing or official provenance.
A packaged binary can still have `is_test=true`. `execution_environment=local`
describes the computer, not the build origin. Missing historical distribution
or test evidence must not be treated as proof of release or non-test usage.

## Event inventory

Installer tags `-lp`, `-dc`, and `-gh` set `install_origin` to `landing_page`,
`documentation`, and `github`. Pass Bash arguments with `bash -s -- -lp`; native
PowerShell accepts the same tags. Untagged commands omit origin unless the
pipeline explicitly sets `OPENSRE_CICD=1`, which records `cicd`. This marker also
sets `is_ci=true` and `cicd_marker=true` on runtime events, independently of vendor
environment detection. An explicit command tag still takes precedence for origin.
The marker is a reported classification, not verified runner identity. `install_source`
still identifies the installer mechanism, and `install_channel` still identifies
the requested `main`/`release` build track.

The first sanitized installation event is saved in `install-events-v1` before
delivery and retained after acknowledgement. Retries reuse that complete event;
a later tagged reinstall cannot replace its origin, including an unknown origin.
The existing installation marker continues to suppress capture for previously
recorded installations.

| Area | Events | Important properties / question answered |
| --- | --- | --- |
| Acquisition | `install_detected`, `account_authenticated`, `cli_command_opensre…` | Install source/origin/build channel/distribution, login conversion, entrypoint, command names, and boolean flags; never raw argument values. Official installers invoke the hidden record-only path immediately after installation. Historical `cli_invoked` records remain valid. |
| Sign-in gate | `sign_in_prompted`, `sign_in_selected`, `stay_signed_out_selected` | The interactive shell's mandatory sign-in screen: one exposure per signed-out launch, then one event per menu round with `choice_label` and `method` (`menu` for a picked option, `dismissed` when the menu was closed without one — Esc, `q`, Ctrl-C, Ctrl-D, or EOF are not distinguished). `sign_in_selected` is recorded before the browser flow starts and is intent only; `account_authenticated` reports the outcome. Already signed-in, non-interactive, and test runs emit none of these. |
| CLI browser authentication | `cli_auth_started` | One event before each browser login attempt, including manual `--no-browser` links. `cli_auth_attempt_id` is a fresh UUID independent of OAuth state and PKCE, also carried in the login URL and automatic `browser_open_requested` event. The web app attaches it to browser page/screen events for this attempt, for at most ten minutes, without merging browser and installation identities. This event establishes intent; a matching browser view establishes receipt. Telemetry opt-out omits both the event and URL identifier. Deploy web-app ingest support before distributing clients that emit this event. |
| Runtime health | `user_id_load_failed`, `sentry_init_skipped` | Identity persistence and telemetry setup failures. |
| Onboarding | `onboard_started`, `onboard_completed`, `onboard_failed` | Funnel conversion, wizard mode, target, provider, and model. |
| Integrations | `integration_setup_started`, `integration_setup_completed`, `integration_verified`, `integration_removed`, `integrations_listed` | Integration adoption and setup/verification conversion by service. |
| Interactive actions | `terminal_actions_planned`, `terminal_actions_executed`, `terminal_turn_summarized` | Planned/executed/success counts, LLM fallback, and session success/fallback buckets. |
| Agent loop | `react_turn_completed` | Phase, iterations, cap hits, stop reason, tool-call count, latency, provider, and model. `stop_reason` reports every hard stop as `iteration_cap`; `loop_stop_reason` keeps the loop's own reason (`goal_unverified`, `stagnation_limit`, `iteration_cap`, `completed`, …). A run that raised adds `error_type` and a redacted, capped `error_message`. |
| Agent tool calls | `agent_tool_call_completed` | Tool/source/role, whether execution occurred, outcome, latency, error state, and termination; never tool arguments or results. A failed call adds the tool's redacted, capped `error_message` and, when known, `blocked_by` (`duplicate_action`, `plan_required`, `menu_pending`, `approval_declined`, `approval_pending`, `hook_exception`), `skipped_by` (`turn_terminated`, `host_cancel`), `exception_type`, the tool's `error_class`, and for an unavailable integration its `error_source` and `setup_command`. `unavailable` is always set; `prompt_turn_id`, `iteration`, and `tool_call_index` place the call in its turn. |
| Ask User | `ask_user_prompt_rendered`, `ask_user_prompt_answered`, `ask_user_prompt_dismissed` | Linked prompt exposure, bounded credential-redacted question/option text, selected option indexes, bounded custom answers, and dismissals with the key class that closed the picker (`dismiss_key`, such as `esc`, `ctrl_c`, or `eof`). Listed answers send indexes only. A rendered prompt carries `reason_code` when its author declared one (`choice` for a menu the model opened, `entry_menu` for a skill's entry menu the host opened); it is never inferred from the question text. |
| Shell and browser | `interactive_shell_rendered`, `browser_open_requested` | First interactive-shell chrome, including the sign-in screen. Not recorded for `--resume`, an auto-launch after `opensre onboard`, or CLI subcommands. `browser_open_requested` is an application-requested browser-open outcome by safe target label. Terminals do not expose whether a manually rendered link was clicked. |
| Agent workflows | `skill_executed`, `skills_release_activated`, `skill_prerequisite_missing`, `opensre_commit_created` | Successful skill entry with the skills release, source (bundled, remote or local override), card version and a 12-character content digest; a process switching to a different skills release; a skill held at entry because a host-owned prerequisite is unmet (`skill`, `check`, `reason_code`, e.g. `credential_missing`); commits produced by supported OpenSRE repair workflows. |
| AI turn | `$ai_generation` | Turn/session IDs, turn kind, model/provider, latency, tokens, integration snapshot, outcome, and error category. It also contains redacted prompt and response text in `$ai_input` and `$ai_output_choices`. An action turn adds the loop's `stop_reason`, the goal review's last refusal (`goal_review_reason`), `last_failed_tool` with its redacted, capped `last_tool_error`, `tool_error_count` (blocked calls included, skipped calls not), and `blocked_tool_calls`. A turn that stopped short (`error_kind=iteration_limit`) repeats those reasons in `$ai_error`. |
| Gateway | `gateway_turn_started`, `gateway_turn_completed`, `gateway_turn_failed` | Surface, answer rate, final intent, latency bucket, and exception type; a failed turn adds a redacted, capped `error_message`. No message body is included. |
| Scheduled work | `scheduled_task_started`, `scheduled_task_completed`, `scheduled_task_failed` | Task kind, provider, status, and task ID. Failed events can contain a capped error string. |
| Updates | `update_started`, `update_completed`, `update_failed` | Check-only vs update, whether a version changed, and failure class. |
| Local-agent safety | `agent_secret_detected`, `agent_killed`, `agent_kill_failed` | Rule names, count, blocked state, agent type, and result; never the detected secret. |
| Suggested loops | `loop_suggestion_prompted`, `loop_suggestion_selected`, `loop_suggestion_skipped` | Picker exposure and selected use case. |
| Onboarding demo | `onboarding_demo_prompted`, `onboarding_demo_selected`, `onboarding_demo_skipped` | Demo exposure, selected option, and whether it was custom. |
| Remote CI repair | `hosted_gateway_started`, `hosted_gateway_healthy`, `remote_ci_monitoring_started`, `test_ci_failure_triggered`, `remote_ci_failure_detected`, `remote_ci_repair_succeeded` | The `delegating-github-ci-repairs` activation path. The signed-in shell records an accepted hosted-gateway start (`already_running`) and every health read that finds the gateway running (`tool_name`). A gateway whose own scheduler runs the repair loop records its registration, the pull request's first CI failure, and a repair commit that passed CI (`attempts`, `duration_ms` since scheduling); the same loop scheduled from the shell records none of them. The seeded demo's failing pull request records `test_ci_failure_triggered` on either host when its repair is scheduled, with `remote`. CI events carry `repair_run_id`, which joins a worker's events to the registration and its prompting `user_id`, plus `repository`, `pr_number`, and `demo`, which is true for the seeded demo run. |
| Execution policy | `repl_execution_policy_decision` | Policy stage, outcome, reason, and planned action count. |

## Product metrics

Eligible installation observations exclude synthetic/test identities and every
identity with confirmed or reported automation in retained runtime history.
Remaining identities have unknown origin: these are observed installations, not
a measured human acquisition denominator. Report their conversion separately
from verified account metrics and show automation counts alongside them.

The first dashboard should keep personal-user and gateway-organization grains
separate. Anonymous IDs are a fallback only for pre-login acquisition:

The existing `analytics_core_metrics.daily_active_users` column measures
authenticated webapp activity. It is not the personal product DAU below, which
must be calculated from `analytics_product_events`.

| Metric | Definition |
| --- | --- |
| Install-to-signup conversion | Eligible installation observations whose first server-verified account link resolves to a Clerk signup created between install and first authentication, divided by all eligible installation observations. |
| Personal activation | Server-resolved users whose linked installation reaches `onboard_completed`, then records a completed, captured AI response with an observed LLM attempt and no error. Legacy events require a real model/provider and non-synthetic output. |
| Gateway activation | Authenticated organizations with an answered `gateway_turn_completed`; do not count gateway actor IDs as users. |
| Onboarding conversion | Distinct eligible installation observations completed, and distinct installations failed, each divided separately by distinct installations started. |
| Personal DAU / WAU / MAU | Distinct server-resolved users with a personal-bearer `cli_command_opensre…` (historically `cli_invoked`) or `$ai_generation` in the requested window. |
| Organization DAU / WAU / MAU | Distinct authenticated organizations with gateway activity in the window, reported separately. |
| D1 / D7 / D30 retention | Personally activated users with another qualifying personal event on the target day/window; compute organization retention separately. |
| Answer rate | Completed gateway turns with `answered=true` divided by completed gateway turns. |
| Action success rate | Sum of `executed_success_count` divided by sum of `executed_count`. |
| LLM fallback rate | `terminal_turn_summarized` events with `fallback_to_llm=true` divided by all summarized turns. |
| Agent reliability | Error, cancellation, and iteration-cap `react_turn_completed` events divided by all ReAct turns. Slice iteration-cap turns by `loop_stop_reason`. |
| Tool-call success | Executed `agent_tool_call_completed` events with `outcome=ok` divided by all executed tool calls; report pre-execution rejection outcomes separately, blocked calls by `blocked_by`. |
| Ask User response rate | Picker-mode `ask_user_prompt_answered` events divided by picker-mode `ask_user_prompt_rendered` events; report dismissals and custom-answer share separately. |
| Latency | p50/p95 of gateway duration, ReAct duration, and `$ai_latency`, sliced by surface/model/provider. |
| Integration adoption | Distinct authenticated organizations completing or verifying setup by service. Personal events use a server-resolved organization; silo events use a bearer-authenticated runtime assertion. Current connected inventory remains a webapp database fact, not an event-derived fact. |
| Scheduled-work reliability | Completed vs failed scheduled tasks by task kind and provider. |
| Feature adoption | Personal users by CLI/AI feature and organizations by gateway surface, without combining identity grains. |

Separate verified/reported automation from unknown-origin installation observations, and keep
it available for automation usage reporting. Report CI detection independently
from actor identity. A non-CI metric requires an explicitly recorded Boolean
`is_ci=false`; missing evidence stays unknown. An audience may deliberately
include unknown traffic, but neither inclusion nor a negative detector result
proves that a human initiated the run.

## Privacy and failure behavior

All delivery honors `OPENSRE_NO_TELEMETRY=1`,
`OPENSRE_ANALYTICS_DISABLED=1`, and `DO_NOT_TRACK=1`. Opting out prevents even
destination credentials from being resolved. Delivery failure never fails the
user command; it is recorded in `~/.opensre/analytics_errors.log`. Non-202
responses include the event name, event ID, HTTP status, and sanitized response
JSON. Only known error codes and the boolean acceptance flag are retained;
echoed payloads, unknown error text, and non-JSON bodies are omitted or redacted.

The webapp rejects oversized or unknown payloads, rate-limits both source IPs
and analytics identities, deduplicates event IDs, and rejects stale, missing,
or modified signatures for authenticated traffic. Anonymous pre-login events
cannot carry a trustworthy shared secret: anyone who owns a client machine can
change open-source client code. Treat raw anonymous install counts as
directional, use the server-verified linked conversion for decisions, and keep
an upstream WAF/rate limit on the public route for network-layer DDoS defense.

`$ai_generation` and `ask_user_prompt_rendered` are the product events
intended to contain user content. Failure text elsewhere (`error_message` on
`agent_tool_call_completed`, `react_turn_completed`, and `gateway_turn_failed`)
is credential-redacted and capped at 500 characters, but can still quote
incident details from a tool or provider. `ask_user_prompt_answered` includes bounded
custom-answer text only; listed answers send option indexes. Ask User text is
credential-redacted and bounded before delivery, but arbitrary incident
details may remain. Treat these fields as confidential, enforce a retention
policy server-side, and keep them out of broad-access product dashboards.
