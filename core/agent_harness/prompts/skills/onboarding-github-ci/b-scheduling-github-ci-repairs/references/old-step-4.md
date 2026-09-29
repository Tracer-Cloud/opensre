<!-- ### Step 4. Create the demo failure (Demo only)

Do exactly three calls, in order. Use a simple demo, e.g. calculator.add incorrectly subtracts, with one fast CI test.

**[1] Create repo**
First check if a demo repo already exists:

`github_cli ["repo", "list", "--limit", "100", "--json", "name,url,createdAt,isPrivate", "--jq", "[.[] | select(.name | startswith(\"opensre-ci-repair-demo-\"))]"]`

If one already exists then reuse in place, if it doesn't exist yet then create a new one:

`github_cli ["repo", "create", "opensre-ci-repair-demo-<random>", "--private", "--add-readme", "--description", "Temporary OpenSRE scheduled CI repair demo"]`

No owner prefix, so the authenticated user keeps deletion rights.

**[2] Populate failing demo into repo**
`seed_demo_repository(repo="<owner>/<repo>")`

Record workspace and `head_sha`.` If it fails, inspect stage and saved progress before retrying. Stop on unexpected local or remote changes.

**[3] Create PR from the intentionally broken branch into main.**
Create the broken CI incident that the demo agent is supposed to repair:

`github_cli ["pr", "create", "--base", "main", "--head", "demo/failing-ci", "--title", "Demo: repair failing calculator CI", "--body", "<BODY_COMES_HERE>]`

With the body constant (BODY_COMES_HERE) defined as "Temporary OpenSRE demo: this branch introduces a regression in `calculator.add` that breaks the `Demo calculator CI` workflow (`python -m unittest -v`). A scheduled OpenSRE repair loop is expected to detect the failing check, push a fix commit to this branch without touching `test_calculator.py`, and turn the checks green. Do not merge; the repository is disposable and can be deleted after the demo."

**Complete this step when:**

- The PR URL is returned to the user. -->