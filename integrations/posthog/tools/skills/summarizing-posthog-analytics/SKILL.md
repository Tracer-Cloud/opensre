---
name: summarizing-posthog-analytics
description: Summarise PostHog product analytics into a per-metric team pulse. Use for PostHog usage overviews, per-metric summaries, product-analytics digests, or "what happened this week" reporting.
tools:
  - list_posthog_tools
  - call_posthog_tool
---

# PostHog Summary

PostHog product-analytics reporting for chat delivery. Produce a per-metric
pulse over a window — what moved, why it matters — not a raw dashboard export.
Single source (PostHog MCP); finish here, then optionally suggest a multi-source
follow-up.

Requires the **PostHog MCP** integration (`posthog_mcp`). The REST-only
`posthog` integration cannot serve this skill.

## 1. Discover

`list_posthog_tools` once with `name_filter: "insights dashboard sql events"` to
find the query surface. Then call `read-data-schema` (or the equivalent schema
tool) BEFORE any aggregation query and only reference events and properties it
confirms exist. Do not reference a property (e.g. `properties.$mcp_error`)
without seeing it in the schema first — HogQL rejects queries against unknown
properties. To pull metric data, use `call_posthog_tool` with
`tool_name: "execute-sql"` and **SQL as a top-level string**:

```json
{
  "tool_name": "execute-sql",
  "arguments": { "query": "SELECT count() FROM events WHERE event = '$pageview'" }
}
```

Never nest the SQL (`{"query": {"query": "SELECT …"}}`) — PostHog rejects that
with `parameter "query" must be of type string`. Use insight/dashboard tools
when the user names a specific dashboard.

## 2. Fetch metrics

Map user words to windows:

- **24h** — "today", "overnight", morning report
- **7d** — "this week", default weekly report
- **30d** — "this month", monthly rollup

Run **one small HogQL query per metric** — do NOT combine multiple metrics into a
single aggregated statement, and never cross-join events against synthetic
"window" rows. That combined shape is the most common cause of PostHog's
`unknown error running this query`. Return **only aggregate numbers** (two
columns: current / previous) — never event-level rows. Compute the two windows
with an explicit conditional aggregate over a single time filter, e.g.:

```sql
SELECT
  countIf(timestamp >= now() - INTERVAL 7 DAY) AS current,
  countIf(timestamp >= now() - INTERVAL 14 DAY
          AND timestamp < now() - INTERVAL 7 DAY) AS previous
FROM events
WHERE event = '$pageview'
```

Swap `event = '...'` (or `count(DISTINCT person_id)` for active users) per
metric. If a query still fails, retry that single metric once with a simpler
`SELECT count() ... WHERE timestamp >= now() - INTERVAL 7 DAY`, then report the
metric as failed rather than aborting the whole report.

Default metric set when the user does not name specific metrics (restrict to
events confirmed present in the schema):

- Active users (`count(DISTINCT person_id)`)
- Key events (`$pageview`, sessions, or the project's top custom events)
- New signups / new users **only** when schema confirms a signup event
  (`user_signed_up`, `signed_up`, `signup`, …). Never substitute
  `user_signed_in` / login / `$identify` for signup or for “signed up”
  retention cohorts — if signup is unverified, skip the metric and say so.
- Notable custom events surfaced by the schema

## 3. Compute per-metric deltas

For each metric: current value, previous value, absolute change, percent change,
and direction (up / down / flat). Flag a metric as notable when the percent
change exceeds ~15% in either direction, or when a value hits zero unexpectedly.

## 4. Summarise (team pulse)

Open with **I found:** a one-line scope summary (window + project + metric
count). Then a per-metric table:

Metric | Current | Previous | Change | % | Trend

- Trend: use up / down / flat words, never rely on colour alone.
- After the table, call out the 1-3 most notable movers with a short "why it
  matters" line and one clear next action when the data supports it.
- When a window returns no data, say so explicitly for that metric — never
  silently widen the window or invent numbers.

## Traps

- HogQL time ranges are relative; state the absolute window in the report.
- Distinguish "metric returned zero" (real) from "query failed" (report the
  failure, do not present it as zero).
- `execute-sql` arguments must be `{"query": "<sql string>"}`. Nested
  `{"query": {"query": "…"}}` fails before any data is read — unwrap and retry
  once, then mark the metric failed if it still errors.
- The MCP server exposes 240+ tools; narrow with `name_filter` before calling —
  never dump the full listing.
- One bounded query per metric group; event-level scans are expensive.
- `unknown error running this query` almost always means the statement was too
  complex (multiple metrics combined, cross-joins, or an unverified property).
  Recover by splitting into one simple per-metric query, not by giving up.
