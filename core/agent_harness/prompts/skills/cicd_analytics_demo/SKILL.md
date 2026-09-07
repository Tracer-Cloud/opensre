---
name: cicd-analytics-demo
description: >-
  CI/CD performance and reliability analytics for one repository over the
  last 30 days: executions, PR failure rate, CI-caused vs source failures,
  developer time blocked, default-branch red time, via
  analyze_github_ci_reliability; also the first-experience demo that scans the
  machine and picks a repository first. Use for "analyze <repo> CI/CD
  performance", "how reliable is our CI", "what does flaky CI cost us". Not for
  listing currently failing checks (github-ci-health). Multi-step; load before
  acting.
---
══════════════════════════════════════════════════════════
CI/CD ANALYTICS DEMO SKILL — interactive-shell action agent:
══════════════════════════════════════════════════════════

WHEN TO USE:
- The user picked "Explore a repo and analyze its CI/CD performance" from the
  startup demo menu, or asks to "run the CI/CD analytics demo", "analyze my
  repo's CI/CD performance", "show me how reliable our CI is", or "how much
  time does CI cost us".
- The user names a repository and asks for its CI/CD performance, reliability,
  failure rate, or downtime.

USE THESE TOOLS:
- `scan_local_git_workspace`
- `analyze_github_ci_reliability`
- `ask_user_choice`

DO NOT USE THIS SKILL FOR:
- Fixing a failing check. Use `github-ci-fix`.
- Setting up the local CI fix loop or prerequisites. Use
  `github-ci-fix-onboarding`.
- Listing the checks that are failing right now. Use `github-ci-health`.

HARD RULES:
- Every number in the reply comes from a tool result. Never estimate, round
  up, or invent executions, failures, rates, or minutes.
- Never run `gh`, `git`, or `shell_run` for this flow; the two tools own
  discovery and analysis end to end and are read-only. The whole demo is
  read-only: no Slack messages, no pushes, no issue writes.
- The scan tool draws the workspace chart in the shell itself. Do not repeat
  the chart or the repository list as text; add one sentence at most.
- `analyze_github_ci_reliability` renders the finished report in the shell.
  Output its `response_text` exactly (one line there) and never retype the
  numbers; continue to the next step.
- If a tool reports a missing GitHub token, say the one command the user runs
  (`opensre integrations setup github`) and offer to continue afterwards. Do
  not fall back to a different data source.
- Decision points use `ask_user_choice` with the exact option texts below.
  End the turn after calling it; the answer arrives as the next user message.

Steps, in order (headers are mandatory, see the labeling rules below).
When the request already names the repository (the startup demo does the scan
and the repository choice itself before submitting), start at step 3 and use
headers [3/4] and [4/4] only.

1) Scan this machine.
   Call `scan_local_git_workspace()` with no arguments. Say in one sentence
   what was found, using `summary` from the result.

2) Pick the repository.
   From the scan result, candidates are repositories with a `github` name and
   `has_workflows` true, ordered by `commits`. Then call `ask_user_choice`
   with title `Which repository should I analyze?` and options, in this order:
   - up to three candidates as `<owner/repo> (<commits> commits, CI configured)`
   - `Use the open-source example repository (Tracer-Cloud/opensre)`
   If there are no candidates, offer only the example repository and say why.
   WAIT for the answer.

3) Analyze CI/CD reliability.
   Call `analyze_github_ci_reliability(owner="<owner>", repo="<repo>")` for the
   chosen repository. In the shell the tool paints the full report itself and
   returns a one-line `summary`; do not restate the figures. Then output the
   tool's `headline` field verbatim as its own line: it already names the
   biggest cost. Do not compute, convert, or reword any figure yourself, and
   do not add a recap, bullet list, or "verified result" of your own after
   the headline: the next assistant text is the step 4 header.

4) Offer what to do next.
   Call `ask_user_choice` with title `What would you like to do next?` and
   these exact options:
   - `Set up an agent that improves CI/CD reliability over time`
   - `Connect OpenSRE to Slack and hand off DevOps chores for your team`
   - `Exit demo`
   WAIT for the answer. On the first option, load `github-ci-fix-onboarding`
   and continue there. On the second, check Slack with the CLI tool
   (`/integrations verify slack`); if it is not configured, run
   `opensre integrations setup slack` through the CLI tool, otherwise say it is
   connected. Then explain in two sentences how to hand off a chore from Slack
   (mention OpenSRE in a channel or DM it). Never post, reply, or send anything
   to Slack in this demo. On `Exit demo`, reply with one line and stop.

Step labeling rules (UX):
- Before every numbered step's tool calls, emit this exact header format as
  assistant text in the SAME response as the tool calls, then one short
  status sentence:
    ### [n/4] <step name>
    <One-sentence status.>
- Never start tool calls for a new step without its header.
