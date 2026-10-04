---
name: PR CI
description: Fixes failing CI checks on open pull requests.
cron: "28 * * * *"
mode: agent
---

Fix failing CI checks on open pull requests.

1. Call summarize_github_pr_status for this repository to find open pull requests whose latest commit has a failed check.
2. Skip drafts, forks, pull requests with merge conflicts, and pull requests pushed to in the last 10 minutes.
3. Pick one and call fix_github_pr_ci with its pr_number to fix it on its own branch.
4. Never force-push or merge, and reply with the pull request link and what changed.
