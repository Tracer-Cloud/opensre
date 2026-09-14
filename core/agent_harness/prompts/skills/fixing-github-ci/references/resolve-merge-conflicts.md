# Resolving merge conflicts

A pull request that conflicts with its base branch gets no checks: GitHub
reports "This branch has conflicts that must be resolved" and the CI never
starts. Bringing the base branch into the PR branch is then the fix.

## Which tool

- Inside a CI repair, `fix_github_pr_ci` merges the base branch itself and
  resolves conflicts before it edits anything; do not add a separate step.
- When the user is in a checkout and asks to resolve, fix, or finish a merge,
  or to commit and push a resolved merge, call `resolve_merge_conflicts`. It
  works on the merge already in progress in the current directory, or pass
  `ref` (for example `origin/main`) to merge that branch first.
- Pass the user's decisions in `instructions` ("keep our version of
  config.py"); they reach the coding agent word for word.

Never run `git merge`, `git add`, `git commit`, or `git push` through
`shell_run` for this; never resolve conflict markers with `code_implement`.

## What the tool does

1. Reads the unmerged paths and hands them to the installed coding agent.
   Lockfiles are regenerated from the resolved manifest, never merged by hand.
2. Verifies that no conflict marker or unmerged path remains.
3. Shows each conflict hunk side by side in the shell: our side, their side,
   and the merged result. `rendered_in_shell` is true when this happened, so
   do not repeat the file contents.
4. Asks the shell's approval policy to commit the merge and push the branch to
   the one it tracks. At `/auto high` (the default) it proceeds; at lower
   levels the user is asked first; an unattended run proceeds. The remote
   branch is never a protected base branch.

## How to reply

- Repeat `outcome` as the first line. It says whether the merge was committed
  and pushed, and it outranks `coding_agent_summary`, which the agent wrote
  before the commit.
- List `resolutions` (one line per file: kept ours, took theirs, combined
  both) so the user sees what was decided.
- End with `next_step`.
- When `error_kind` is `conflicts_remain`, name `unresolved_files`, ask the
  user how to settle each, and call the tool again with their answer in
  `instructions`. The merge stays in progress; do not abort it.
- When `error_kind` is `confirmation_denied`, say the resolved files are in the
  working tree uncommitted and ask what should change.
- When `error_kind` is `push_failed`, the merge is committed locally; give the
  push command from `next_step`.
