---
name: Merge conflicts
description: Resolves merge conflicts on open pull requests.
cron: "28 * * * *"
mode: agent
---

Resolve merge conflicts on open pull requests.

1. List open non-draft pull requests from branches in this repository that have merge conflicts.
2. Skip pull requests with a push in the last 10 minutes.
3. Pick one, merge the default branch into its branch, and resolve each conflict keeping the author's intent.
4. Run the relevant tests locally and push the merge commit.
5. If a conflict needs a product decision, comment on the pull request explaining it and move on.
6. Never force-push or merge, and reply with the pull request link and what you resolved.
