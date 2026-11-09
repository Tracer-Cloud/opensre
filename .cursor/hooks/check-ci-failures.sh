#!/usr/bin/env bash
# On agent stop: if this branch has an open PR with failing required checks,
# auto-submit a follow-up so CI / test failures get fixed before the task ends.
# See AGENTS.md "CI failures and tests" and CI.md §8.
set -euo pipefail

input="$(cat || true)"
status="$(printf '%s' "$input" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status") or "")' 2>/dev/null || true)"
loop_count="$(printf '%s' "$input" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(int(d.get("loop_count") or 0))' 2>/dev/null || echo 0)"

if [[ "$status" == "aborted" || "$status" == "error" ]]; then
  printf '%s\n' '{}'
  exit 0
fi

# Cap automatic remediation loops (hooks.json loop_limit is a second backstop).
if [[ "${loop_count:-0}" -ge 2 ]]; then
  printf '%s\n' '{}'
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  printf '%s\n' '{}'
  exit 0
fi

# Only act when we are on a branch that already has an open PR.
pr_json="$(gh pr view --json number,url,statusCheckRollup 2>/dev/null || true)"
if [[ -z "$pr_json" ]]; then
  printf '%s\n' '{}'
  exit 0
fi

export PR_URL FAIL_LINES
PR_URL="$(printf '%s' "$pr_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("url") or "")')"
FAIL_LINES="$(printf '%s' "$pr_json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
failed = []
for row in data.get("statusCheckRollup") or []:
    conclusion = (row.get("conclusion") or "").upper()
    name = row.get("name") or row.get("context") or "unknown"
    if conclusion in {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
        failed.append("- " + name)
print("\n".join(failed))
')"

if [[ -z "${FAIL_LINES}" ]]; then
  printf '%s\n' '{}'
  exit 0
fi

python3 -c '
import json, os
pr_url = os.environ["PR_URL"]
fail_lines = os.environ["FAIL_LINES"]
msg = (
    f"CI checks are still failing on {pr_url}.\n\n"
    f"Failing checks:\n{fail_lines}\n\n"
    "Mandatory (see AGENTS.md — CI failures and tests):\n"
    "1. `gh run view --log-failed` (or open the failed job) and identify the root cause.\n"
    "2. Fix product/test code; do not skip tests or use constant-condition toggles.\n"
    "3. Re-run the focused local checks from CI.md for the modules you touched.\n"
    "4. Push, then `gh pr checks` until required jobs are green (CI Gate included).\n"
    "5. Address Greptile / review threads per CI.md §8.\n\n"
    "Do not stop until the failures above are fixed or you have a concrete blocker for the user."
)
print(json.dumps({"followup_message": msg}))
'

exit 0
