---
name: cicd-analytics-demo
description: >-
  First-experience demo: scan the machine for git repositories, pick a suitable
  real one, show CI/CD reliability KPIs and developer time blocked by unreliable
  CI via analyze_github_ci_reliability, then offer next steps. Multi-step; load
  before acting.
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
  discovery and analysis end to end and are read-only.
- The scan tool draws the workspace chart in the shell itself. Do not repeat
  the chart or the repository list as text; add one sentence at most.
- `analyze_github_ci_reliability` returns the finished report in
  `response_text`. Output it exactly, then continue to the next step.
- If a tool reports a missing GitHub token, say the one command the user runs
  (`opensre integrations setup github`) and offer to continue afterwards. Do
  not fall back to a different data source.
- Decision points use `ask_user_choice` with the exact option texts below.
  End the turn after calling it; the answer arrives as the next user message.

Steps, in order (headers are mandatory, see the labeling rules below):

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
   chosen repository. Output `response_text` exactly. Then add one sentence
   that names the single biggest cost in plain words, taken from the report
   (for example the blocked developer time or the longest breakage).

4) Offer what to do next.
   Call `ask_user_choice` with title `What would you like to do next?` and
   these exact options:
   - `Set up an agent that improves CI/CD reliability over time`
   - `Connect OpenSRE to Slack and hand off DevOps chores for your team`
   - `Exit demo`
   WAIT for the answer. On the first option, load `github-ci-fix-onboarding`
   and continue there. On the second, run the Slack integration setup
   (`opensre integrations setup slack`) through the CLI tool and explain the
   handoff in two sentences. On `Exit demo`, reply with one line and stop.

Step labeling rules (UX):
- Before every numbered step's tool calls, emit this exact header format as
  assistant text in the SAME response as the tool calls, then one short
  status sentence:
    ### [n/4] <step name>
    <One-sentence status.>
- Never start tool calls for a new step without its header.
