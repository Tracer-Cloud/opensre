---
name: tracking-github-work-status
description: Use GitHub workflow tools to read work status, draft reports, summarize follow-ups, and execute only approved issue mutations.
tools:
  - list_github_work_items
  - summarize_github_pr_status
  - list_github_security_alerts
  - generate_work_status_report
  - summarize_community_followups
  - propose_github_issue_mutation_from_slack
  - execute_github_issue_mutation
---

# GitHub Workflow

Use this workflow when the user asks about GitHub engineering status, PR readiness,
community follow-ups, or turning an explicit Slack request into a GitHub issue.

1. Read before reporting. Use the read-only GitHub tools first, and treat missing
   or failed reads as incomplete status rather than proof that there are no
   blockers.
2. Report or summarize from known data. Use `generate_work_status_report` for
   Slack-ready status and `summarize_community_followups` for unanswered
   contributor questions or agenda items.
3. Propose mutations before execution. Use
   `propose_github_issue_mutation_from_slack` only for explicit Slack-sourced
   create, update, or close requests.
4. Execute only through approval. `execute_github_issue_mutation` is the only
   mutating GitHub workflow tool. It is never an investigation action and must
   remain chat-only, approval-gated, and proposal-driven.
5. For ad-hoc `gh` (flexible issue/PR/api ops outside this Slack proposal flow),
   use `github_cli` — never `shell_run` with raw `gh`.
