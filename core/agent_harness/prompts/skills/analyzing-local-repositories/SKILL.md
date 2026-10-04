---
name: analyzing-local-repositories
description: >-
  Shows what the user's local repositories say about how they work, read from git and CI files on this
  machine with no GitHub token or network: follow-up fixes, CI trial and error, AI co-authorship, workflow
  hygiene, tests and loose ends. Use when GitHub is not connected or cannot be read during onboarding, when
  the user's repositories have no GitHub Actions, or when the user asks what their commits or local
  repositories say about them. For CI run history and failure rates, use analyzing-github-ci-performance.
metadata:
  owner: Vincent
  last_changed_by: Vincent
  last_changed_at: 2026-10-04
  usecases:
  - For first-time users whose GitHub connection is missing or failing, so onboarding still shows insights from their own repositories.
  - For developers whose repositories have no GitHub Actions or run another CI service.
  - For users asking what their local commits and CI files say about their habits.
  requires:
  - The analyze_local_repositories tool, which reads local git checkouts with no credentials or network.
  - At least one local git checkout with recent commits; otherwise the skill offers to look in another folder.
  - For the follow-up menu, an interactive terminal; elsewhere the options are listed as text.
  version: '1.0'
---

# Local repository insights

Show the user what their own repositories say about how they work, read from
git on this machine with no GitHub token and no network, then offer the next
step. This often runs in a user's first session, after GitHub could not be
used, so it must end with something about their own work, never with only a
blocker.

## Plan

After reading this skill, use `update_plan` to create the live Local Insights
plan from the four numbered workflow headings below. Mark a step `in_progress`
or `completed` in the same response as that step's tool call. Mark step 1 with
`verifies: true` and step 2 with `deliverable: true` in every `update_plan`
call: step 2's work is the reply itself.

- [ ] Step 1. Read the local repositories with analyze_local_repositories.
- [ ] Step 2. Show the local insights report as a text-only reply.
- [ ] Step 3. Use ask_user_choice to ask what to watch and what comes next.
- [ ] Step 4. Save what to watch with memory_remember and start the chosen next step.

## Workflow

### 1. Read the local repositories

Call `analyze_local_repositories` with:

- `reason`:
  - `github_not_connected` when the user chose their local repositories at the
    GitHub setup menu, or GitHub has no token;
  - `github_failed` when `analyze_github_ci_reliability` could not read GitHub;
    pass its `error_kind` as `github_error` when it named one;
  - `no_github_actions` when the user picked a repository marked `(local insights)`;
  - `requested` otherwise.
- `repository`: the repository the user picked earlier in this session, as
  `owner/repo` or its local path, if any.
- `paths`: the `path` of each repository in this session's
  `scan_local_git_workspace` result, the picked repository's own path first,
  then the most active ones, when a scan ran. Omit it otherwise; the tool scans.

Complete when `analyze_local_repositories` has returned in this turn. If it
returned `cancelled`, stop. If it returned `success: false` with an `error`,
say in one line that the local repositories could not be read, and stop. If
this build has no `analyze_local_repositories` tool, say in one line that
`opensre update` adds this report, and stop.

### 2. Show the report

Reply with the report below as Markdown text and nothing else; call no tool in
that message. The host shows it, then the plan continues with step 3.

Every number comes from the tool result: never estimate, round, or add one.
The tool returns no commit messages or code, so never quote any.

1. When `reason` is `github_not_connected` or `github_failed`, open with one
   line that names the blocker in plain words and says this report needs no
   GitHub, for example: "GitHub couldn't be read from this machine, so here's
   what your local history shows instead." Nothing more about the blocker.
2. One line: `<repositories> repositories · <own_commits> commits by you in the
   last <days> days. Read from git on this machine; no code or commit messages
   left it.`
3. A `What stands out` heading, then the first three `insights` whose label is
   not `Rhythm`, in the order given. Each is one bullet: the `label` in bold
   with a colon, the `fact` unchanged, then one short clause from the table
   below.
4. When a `Rhythm` insight is present, one line after the bullets: its `fact`.
5. When `reason` is not `requested`, one line: `Connect GitHub to add:` and the
   `github_only_metrics` joined with ` · `.
6. Each `coverage_notices` entry on its own line.

| Label | Clause to add |
|---|---|
| Retired actions | Moving to the current version fixes it; OpenSRE can open that pull request once GitHub is connected. |
| Follow-up fixes | Quick re-fixes usually mean a problem surfaced after the commit, often in CI; OpenSRE fixes failing checks on your pull requests. |
| CI trial and error | Every attempt costs a CI run; OpenSRE reproduces and repairs CI failures for you. |
| AI pairing | Agents write code faster than CI can keep up with; OpenSRE keeps CI green at that pace. |
| Workflow hygiene | A tag can be moved to new code, as in the 2025 tj-actions compromise; timeouts and concurrency groups cap wasted runner time. |
| No CI | Nothing checks these commits before they land. |
| Tests with code | Code changes without tests are where regressions slip through. |
| Local checks | Running the framework's install command catches these before CI does. |
| Reverts | Each revert is a change that reached the branch and had to come back out. |
| Loose ends | Stale branches and stashes hide unfinished work. |
| Large commits | Large changes are slower to review and harder to revert. |
| Other CI | OpenSRE's CI analysis reads GitHub Actions today. |

When `repositories` is 0, replace items 2 to 6 with one line saying no local
repositories with commits in that window were found, and, when
`skipped_protected` lists folders, that those folders were not searched.

Complete when the reply containing the report has been shown.

### 3. Ask what to watch and what comes next

Call `ask_user_choice` once with two `questions`:

1. `label` `Watch`, `title` `Which of these should OpenSRE keep an eye on?`,
   `multi_select` true, options: the labels of the insights shown under
   `What stands out` in step 2, then `None of these`.
2. `label` `Next`, `title` `What next?`, options from the first row that
   applies, then `Not now`:

| When | Options |
|---|---|
| `repositories` is 0 and `skipped_protected` lists folders | `Look in <folder>` for each listed folder |
| `reason` is `github_not_connected` | `Connect GitHub and get the CI report` |
| `github_error` is `tls_untrusted` | `Show me how to update OpenSRE` |
| `github_error` is `unauthorized` or `not_found` | `Reconnect GitHub`, `Pick another repository` |
| `reason` is `github_failed` | `Try the CI report again` |
| `reason` is `no_github_actions` | `Watch OpenSRE fix a failing check in a demo repo (under 5 min)` |
| otherwise | `Get the CI report for one of these repositories` |

When step 2 showed nothing under `What stands out` (for example `repositories`
is 0), ask only the `Next` question, as `title` and `options`. End the turn
after the call. If the result says the menu is
unavailable, list the questions with numbered options as text and wait.

Complete when the `ask_user_choice` call has returned in this turn. The
answers arrive as the next user message.

### 4. Remember and start the next step

When the `Watch` answer names insights, call `memory_remember` with `name`
`local-insights-watch`, `type` `preference`, a one-line `description`, and
`content` listing the chosen labels and today's date, in the same response as
the next action below.

Then act on the `Next` answer:

- `Connect GitHub and get the CI report`, `Try the CI report again`,
  `Pick another repository`, or `Get the CI report for one of these
  repositories`: call `skill_view(name="analyzing-github-ci-performance")` and
  follow it.
- `Reconnect GitHub`: call `slash_invoke` with command `/integrations` and
  args `["setup", "github"]`, then end the turn.
- `Show me how to update OpenSRE`: reply in one line that `opensre update`
  installs the latest build, and to ask again afterwards.
- `Watch OpenSRE fix a failing check in a demo repo (under 5 min)`: call
  `skill_view(name="scheduling-github-ci-repairs")` and follow it.
- `Look in <folder>`: call `analyze_local_repositories` again with `root` set
  to that folder and the same `reason`, then repeat steps 2 and 3.
- `Not now`: acknowledge in one line and conclude.

Complete when the chosen next step has started or the user declined.
