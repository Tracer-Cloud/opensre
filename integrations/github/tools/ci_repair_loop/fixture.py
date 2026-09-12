"""Create a private reusable fixture and isolate each demo PR from its healthy baseline."""

from __future__ import annotations

import hashlib
import json
from http import HTTPStatus
from typing import Any

from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.tools.ci_repair_loop.models import RepairRun
from integrations.github.tools.ci_repair_loop.storage import RepairStore


def baseline_files(base_branch: str) -> dict[str, str]:
    """The marker and small healthy fixture that a reusable repository must contain."""
    return {
        ".opensre-demo.json": '{"kind":"opensre-ci-repair-demo","version":1}\n',
        ".gitignore": "__pycache__/\n.pytest_cache/\n",
        "calculator.py": "def add(a, b):\n    return a + b\n",
        "test_calculator.py": (
            "import unittest\nfrom calculator import add\n\n"
            "class CalculatorTest(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
        ),
        "AGENTS.md": (
            "Run `python -m unittest -v`. Fix calculator.py; preserve the test, "
            "workflow, and demo marker.\n"
        ),
        ".github/workflows/test.yml": (
            "name: Calculator test\non:\n  push:\n    branches: ["
            + json.dumps(base_branch)
            + "]\n  pull_request:\npermissions:\n  contents: read\njobs:\n"
            "  test:\n    runs-on: ubuntu-latest\n    timeout-minutes: 1\n"
            "    steps:\n      - uses: actions/checkout@v4\n"
            "      - run: python3 -m unittest -v\n"
        ),
    }


def object_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("GitHub returned an unexpected response.")
    return value


def _blob_sha(content: str) -> str:
    data = content.encode()
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()


def validate_baseline(client: GitHubRestClient, run: RepairRun) -> str:
    """Refuse an unmarked, modified, or incompatible fixture before changing it."""
    path = f"repos/{run.owner}/{run.repo}"
    branch = object_response(client.request("GET", f"{path}/branches/{run.base_branch}"))
    sha = str(branch["commit"]["sha"])
    tree = object_response(
        client.request("GET", f"{path}/git/trees/{sha}", params={"recursive": 1})
    )
    entries = {entry["path"]: entry for entry in tree.get("tree", [])}
    allowed = set(baseline_files(run.base_branch)) | {"README.md"}
    unexpected = any(
        entry.get("type") != "tree" and name not in allowed for name, entry in entries.items()
    )
    if (
        tree.get("truncated")
        or unexpected
        or any(
            entries.get(name, {}).get("sha") != _blob_sha(content)
            or entries.get(name, {}).get("mode") != "100644"
            for name, content in baseline_files(run.base_branch).items()
        )
    ):
        raise ValueError("The fixed repository name belongs to an unrecognized or modified demo.")
    return sha


def _commit_files(
    client: GitHubRestClient,
    run: RepairRun,
    parent: str,
    files: dict[str, str],
    message: str,
) -> str:
    path = f"repos/{run.owner}/{run.repo}/git"
    original = object_response(client.request("GET", f"{path}/commits/{parent}"))
    tree = object_response(
        client.request(
            "POST",
            f"{path}/trees",
            body={
                "base_tree": original["tree"]["sha"],
                "tree": [
                    {"path": name, "mode": "100644", "type": "blob", "content": content}
                    for name, content in files.items()
                ],
            },
        )
    )
    commit = object_response(
        client.request(
            "POST",
            f"{path}/commits",
            body={
                "message": message,
                "tree": tree["sha"],
                "parents": [parent],
            },
        )
    )
    return str(commit["sha"])


def prepare_demo(client: GitHubRestClient, run: RepairRun, store: RepairStore) -> None:
    """Create once, validate on reuse, then create or recover only this run's PR."""
    path = f"repos/{run.owner}/{run.repo}"
    try:
        repository = object_response(client.request("GET", path))
    except GitHubApiError as exc:
        if exc.status_code != HTTPStatus.NOT_FOUND:
            raise
        target = (
            "user/repos"
            if run.owner.casefold() == run.actor.casefold()
            else f"orgs/{run.owner}/repos"
        )
        repository = object_response(
            client.request(
                "POST",
                target,
                body={
                    "name": run.repo,
                    "private": True,
                    "auto_init": True,
                    "description": "Reusable OpenSRE CI repair onboarding demo",
                },
            )
        )
        run.repository_id = int(repository["id"])
        run.created_repository = True
        store.save(run)
    if (
        str(repository.get("full_name", "")).casefold() != f"{run.owner}/{run.repo}".casefold()
        or repository.get("private") is not True
        or repository.get("fork")
        or not object_response(repository.get("permissions", {})).get("push")
    ):
        raise ValueError(
            "The demo requires write access to the selected private, non-fork repository."
        )
    run.base_branch = str(repository["default_branch"])
    # Only the run that created this exact repository may initialize its baseline.
    if run.created_repository and run.repository_id == repository["id"]:
        try:
            baseline = validate_baseline(client, run)
        except ValueError:
            # Initialization is allowed only when the repo still contains its generated README.
            entries = client.request("GET", f"{path}/contents")
            if not isinstance(entries, list) or {item.get("name") for item in entries} != {
                "README.md"
            }:
                raise
            branch = object_response(client.request("GET", f"{path}/branches/{run.base_branch}"))
            parent = str(branch["commit"]["sha"])
            baseline = _commit_files(
                client, run, parent, baseline_files(run.base_branch), "Seed healthy CI demo"
            )
            client.request(
                "PATCH",
                f"{path}/git/refs/heads/{run.base_branch}",
                body={"sha": baseline, "force": False},
            )
    else:
        baseline = validate_baseline(client, run)
    if not run.initial_sha:
        run.branch = f"demo/ci-repair-{run.id}"
        run.initial_sha = _commit_files(
            client,
            run,
            baseline,
            {"calculator.py": "def add(a, b):\n    return a - b\n"},
            "Demo: expose an addition bug with a real test",
        )
        store.save(run)
    try:
        client.request("GET", f"{path}/git/ref/heads/{run.branch}")
    except GitHubApiError as exc:
        if exc.status_code != HTTPStatus.NOT_FOUND:
            raise
        client.request(
            "POST",
            f"{path}/git/refs",
            body={"ref": f"refs/heads/{run.branch}", "sha": run.initial_sha},
        )
    if not run.pr_number:
        existing = client.request(
            "GET", f"{path}/pulls", params={"state": "open", "head": f"{run.owner}:{run.branch}"}
        )
        pr = (
            existing[0]
            if isinstance(existing, list) and existing
            else client.request(
                "POST",
                f"{path}/pulls",
                body={
                    "title": "OpenSRE demo: repair calculator CI",
                    "head": run.branch,
                    "base": run.base_branch,
                    "body": "Scheduled CI repair demo. The agent must fix the source while preserving the test.",
                },
            )
        )
        run.pr_number = int(object_response(pr)["number"])
        store.save(run)


def cleanup_demo(client: GitHubRestClient, run: RepairRun) -> None:
    """Close the completed demo PR and remove only its unique branch."""
    if not run.demo or not run.checks_passed or run.branch != f"demo/ci-repair-{run.id}":
        return
    path = f"repos/{run.owner}/{run.repo}"
    client.request("PATCH", f"{path}/pulls/{run.pr_number}", body={"state": "closed"})
    try:
        client.request("DELETE", f"{path}/git/refs/heads/{run.branch}")
    except GitHubApiError as exc:
        if exc.status_code != HTTPStatus.NOT_FOUND:
            raise
    run.cleanup = "Demo PR closed and branch removed; reusable repository retained."
