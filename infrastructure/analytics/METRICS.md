# Product analytics metrics

The cross-store installation key is the random `analytics_id` from
`~/.opensre/anonymous_id`. A personal bearer adds the server-resolved Clerk user
and organization. A silo bearer adds an authenticated organization assertion
but no Clerk user; chat actor IDs remain event properties and must not be counted
as people.

The current ClickHouse views implement the installation funnel and account/session
headlines only. In particular, `analytics_core_metrics.daily_active_users` is
authenticated webapp activity, not the personal product DAU defined below. The
remaining metrics require queries over `analytics_product_events`.

## Acquisition and activation

| Metric | Events and calculation |
| --- | --- |
| Installations | Distinct non-CI `analytics_id` values with `install_detected`. |
| Install-to-signup conversion | Installations linked to a Clerk signup created between `install_detected` and the first authenticated link, divided by installations. |
| Authenticated installations | Distinct non-CI installations with any later personal-bearer event. `account_authenticated` is the normal first link, but the metric does not depend on that single event being delivered. |
| Sign-in gate conversion | Distinct non-CI installations with `sign_in_selected`, and distinct installations with `stay_signed_out_selected`, each divided separately by distinct installations with `sign_in_prompted`. Slice `stay_signed_out_selected` by `method` to separate the explicit exit option (`menu`) from a closed menu (`dismissed`). A `sign_in_selected` without a later `account_authenticated` is an abandoned or failed browser login. |
| Onboarding conversion | Distinct non-CI installations completing onboarding, and distinct installations failing onboarding, each divided separately by distinct installations that started. |
| Personal activation | Server-resolved users whose linked installation completes onboarding and later records a completed, captured AI response with an observed LLM attempt and no error. Legacy events require a real model/provider and non-synthetic output. |
| Gateway activation | Organizations with an authenticated `gateway_turn_completed` where `answered=true`. Keep this separate from personal activation because a gateway actor is not a Clerk user. |

## Usage and retention

| Metric | Events and calculation |
| --- | --- |
| Personal DAU / WAU / MAU | Distinct server-resolved users with a personal-bearer `cli_invoked` or `$ai_generation` in the requested window. |
| Organization DAU / WAU / MAU | Distinct authenticated organizations with gateway activity in the requested window; report separately from personal users. |
| D1 / D7 / D30 retention | Personally activated users with another qualifying personal event in the target day or window. Organization retention is a separate gateway metric. |
| Feature adoption | Personal users by CLI/AI feature and organizations by gateway surface; never combine the two identity grains. |
| Integration adoption | Distinct authenticated organizations completing or verifying setup, grouped by service. Personal events carry a server-resolved organization; silo events carry a bearer-authenticated runtime assertion. |

## Reliability and quality

| Metric | Events and calculation |
| --- | --- |
| Gateway answer rate | `gateway_turn_completed` with `answered=true`, divided by all completed gateway turns. |
| Terminal action success | Sum of `executed_success_count`, divided by sum of `executed_count`. |
| LLM fallback rate | `terminal_turn_summarized` with `fallback_to_llm=true`, divided by all summarized turns. |
| Agent reliability | Error, cancellation, and iteration-cap ReAct turns, divided by all `react_turn_completed` events. |
| Tool-call success | Executed `agent_tool_call_completed` events with `outcome=ok`, divided by all executed tool calls. Slice pre-execution rejection outcomes separately. |
| Ask User response | `ask_user_prompt_answered` divided by picker-mode `ask_user_prompt_rendered`; report `ask_user_prompt_dismissed` and custom-answer share separately. |
| Scheduled-work reliability | Completed versus failed scheduled tasks by task kind and provider. |
| Latency | p50/p95 for gateway, ReAct, and AI-generation duration by surface, model, and provider. |

Non-CI metrics require recorded Boolean `is_ci=false`; missing flags remain
unknown. A detector negative is not human verification. Anonymous install
and onboarding counts are directional because a public open-source client
cannot keep a signing secret from its machine owner. Use personal-bearer linkage
for trusted user metrics, and never substitute a gateway actor ID for a Clerk
user ID.
