# GitHub Actions (maintainer reference)

Internal notes for repository automation under `.github/workflows/`. Not published on the docs site.

## Workflows

| Workflow | Purpose |
| -------- | ------- |
| [`ci.yml`](ci.yml) | PR/push quality gates and sharded pytest |
| [`ci-labels-windows.yml`](ci-labels-windows.yml) | Optional Windows CI (`ci:windows` label) |
| [`codeql.yml`](codeql.yml) | Full post-merge CodeQL and manual PR-profile benchmarks |
| [`greptile-pr-reminder.yml`](greptile-pr-reminder.yml) | Greptile review nudge on PR open |
| [`celebrate-merged-pr.yml`](celebrate-merged-pr.yml) | Post-merge celebration comment |
| [`good-first-issue-assign.yml`](good-first-issue-assign.yml) | Auto-assign good first issues |
| [`release.yml`](release.yml) | Release builds and artifacts |
| [`installer-canary.yml`](installer-canary.yml) | Queued post-publish canaries for the public install paths (CDN, GitHub release resolution, PowerShell, Homebrew) on Linux/macOS/Windows; cycles are globally serialized because they share one failure-tracking issue |

See [CI.md](../../CI.md) for local parity commands before push.

## CodeQL ownership

The checked-in workflow owns CodeQL for this repository. Full Python and
JavaScript/TypeScript `security-and-quality` scans run on `main` and weekly. The
manual `pr-fast` profile uses default queries and shipped Python paths only;
select a larger runner by passing its label through `runner_label`.

Keep repository-level GitHub Code Quality disabled after this workflow lands.
Otherwise GitHub starts a second dynamic Python/JavaScript analysis on every PR
and push, restoring the four-minute critical path and duplicating scan cost.
