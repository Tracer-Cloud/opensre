---
name: measuring-github-star-velocity
description: Compute GitHub repository star velocity over a rolling time window using the stargazers API.
tools:
  - execute_python_code
---

# GitHub Star Velocity

Use this skill only when the dedicated `get_github_star_history` tool is not
available. When that tool is available, prefer it for GitHub star velocity,
stars gained in a recent window, day-by-day stars, star growth rate, or similar
metrics for a repository.

## Prerequisites

1. Set `allow_network: true` on `execute_python_code`.
2. Read `GITHUB_TOKEN` from the subprocess environment (injected when GitHub
   credentials are configured). Never print the token.
3. Parse `owner/repo` from the repository URL or user input.

## API approach

1. Fetch current `stargazers_count` from `GET /repos/{owner}/{repo}`.
2. Fetch stargazers with timestamps from
   `GET /repos/{owner}/{repo}/stargazers?per_page=100` using
   `Accept: application/vnd.github.v3.star+json`.
3. Each entry includes `starred_at`. Count stars where `starred_at` is within
   the requested window.

## Critical pagination trap

Stargazers are returned **oldest first**, not newest first. Do **not** stop
after the first page when the newest entries on that page fall outside the
window — that produces a false zero.

For responsiveness, compute the last page from `stargazers_count` and
`per_page=100`, fetch newest pages by explicit `page` number in descending
order, and stop once the oldest `starred_at` on a fetched page is before the
window start. Only run a full historical scan when the user explicitly asks for
all-time history or an audit-quality export.

## Sanity check before reporting

Treat these results as suspicious and rerun with a full paginated scan before
answering:

- `0` stars in the last 24 hours for a repo with thousands of stars and recent
  activity
- Velocity that implies losing stars without an explicit unstar request
- Counts that disagree sharply with `stargazers_count` deltas when you have a
  recent baseline

When a result looks wrong, widen the scan (all pages), print the most recent
`starred_at` observed, and only then return the velocity.

## Output shape

Return:

- Total current stars
- Stars gained in the window
- Velocity (stars per hour and stars per day)
- Window start timestamp (UTC)
- Most recent `starred_at` in the window
- Optional hourly breakdown for the last 24 hours
