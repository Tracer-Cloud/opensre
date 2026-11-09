---
name: summarizing-sentry-issues
description: Summarise Sentry issues by theme and impact with optional uptime rollup. For overviews, morning digests, reliability questions.
tools:
  - search_sentry_issues
  - get_sentry_issue_details
  - list_sentry_issue_events
  - get_sentry_uptime_digest
---

# Sentry Summary

Sentry-only (#3 on-demand, #10 morning digest). Multi-source: finish Sentry
here, then suggest a multi-source investigation.

## 1. Fetch issues

`search_sentry_issues` with `query: "is:unresolved"`:
- **24h** — morning digest, overnight (#10); **7d** — "this week", overview.

Max 100 groups per page. Use `digest.page_saturated` vs `page_complete`.
When `completeness` is `empty`, report zero — do not widen unless asked.

## 2. Fetch uptime

`get_sentry_uptime_digest`. Skip if `uptime_watch_active` is false. Include
still-down **before** Issues, recovered **after** Issues.

## 3. Classify

`digest.structural_clusters` → business themes with `sample_short_ids`.

## 4. Rank

`digest.priority_candidates` + `business_impact_score`, not raw `count`.
Prefer userCount, blockers, regressions; cite `impact_reasons`.

## 5. Enrich

Top 3–5 only: `get_sentry_issue_details` + `list_sentry_issue_events` (limit 10).

## 6. Summarise

- Header + summary bullet counts (Issues + Uptime).
- `[DOWN]` above Issues; Issues clusters + priority table; `[RECOVERED]` after.

## Traps

- `count` = events/group; `issue_count` = groups returned
- `page_saturated` = first page; `completeness: empty` = zero groups
- Uptime needs a running watch schedule — no watch = no data
