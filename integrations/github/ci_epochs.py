"""Reconstruct GitHub Actions epochs from ordered PR commits and workflow history.

Run ``uv run python -m integrations.github.ci_epochs owner/repo#123 --once``.
Add ``--publish`` to send confirmed agent fixes through authenticated analytics.
Git authorship identifies the fixer; the ingest server resolves account ownership.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.constants.git import OPENSRE_COMMIT_COAUTHOR_EMAIL
from infrastructure.analytics.events import Event
from infrastructure.analytics.provider import get_analytics, shutdown_analytics
from infrastructure.observability.errors.sentry import capture_exception
from integrations.github.client import GitHubRestClient
from integrations.github.tools.ci_repair_loop.credentials import configured_token

_FAILED = {
    "failure",
    "error",
    "cancelled",
    "timed_out",
    "action_required",
    "startup_failure",
    "stale",
}
_PASSED = {"success", "skipped", "neutral"}


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    result: str
    settled_at: str = ""


@dataclass(frozen=True)
class Epoch:
    id: str
    commits: tuple[Commit, ...]

    @property
    def fixing_commit(self) -> Commit | None:
        return self.commits[-1] if self.commits[-1].result == "green" else None

    @property
    def outcome(self) -> str:
        fixing = self.fixing_commit
        if fixing is None:
            return "unresolved"
        if any(commit.result not in {"red", "green"} for commit in self.commits):
            return "incomplete"
        return "agent_fixed" if fixing.author == "opensre" else "other_fixed"

    def record(self) -> dict[str, Any]:
        fixing = self.fixing_commit
        return {
            "id": self.id,
            "commits": [asdict(commit) for commit in self.commits],
            "first_red_sha": self.commits[0].sha,
            "first_green_sha": fixing.sha if fixing else None,
            "fixing_ci_updated_at": fixing.settled_at if fixing else None,
            "fixing_sha": fixing.sha if fixing else None,
            "fixer": fixing.author if fixing else None,
            "outcome": self.outcome,
        }


def build_epochs(repository: str, pr_number: int, commits: list[Commit]) -> list[Epoch]:
    """Reduce one ordered snapshot; only a settled green closes a red streak."""
    epochs: list[Epoch] = []
    current: list[Commit] = []
    for commit in commits:
        if not current and commit.result != "red":
            continue
        current.append(commit)
        if commit.result == "green":
            epochs.append(_epoch(repository, pr_number, current))
            current = []
    if current:
        epochs.append(_epoch(repository, pr_number, current))
    return epochs


def _epoch(repository: str, pr_number: int, commits: list[Commit]) -> Epoch:
    identity = f"{repository.casefold()}#{pr_number}:{commits[0].sha}"
    return Epoch(hashlib.sha256(identity.encode()).hexdigest(), tuple(commits))


def commit_author(row: dict[str, Any]) -> str:
    """Recognize the agent's exact email, including a Git co-author trailer."""
    commit = row["commit"]
    identities = [commit.get("author") or {}, commit.get("committer") or {}]
    email = OPENSRE_COMMIT_COAUTHOR_EMAIL.casefold()
    footer = str(commit.get("message") or "").rstrip().rsplit("\n\n", 1)[-1]
    coauthors = re.findall(r"^Co-authored-by:\s*[^\n<>]+<([^\n<>]+)>\s*$", footer, re.I | re.M)
    if any(
        str(identity.get("email", "")).casefold() == email for identity in identities
    ) or email in {value.casefold() for value in coauthors}:
        return "opensre"
    return str((row.get("author") or {}).get("login") or identities[0].get("name") or "unknown")


def workflow_results(runs: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """Join each SHA to the latest attempt of every workflow/event, never empty green."""
    latest: dict[tuple[str, int, str], dict[str, Any]] = {}
    for run in runs:
        key = (run["head_sha"], run["workflow_id"], run["event"])
        previous = latest.get(key)
        version = (run["id"], run.get("run_attempt", 1))
        if previous is None or version > (previous["id"], previous.get("run_attempt", 1)):
            latest[key] = run
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (sha, _, _), run in latest.items():
        grouped[sha].append(run)
    result = {}
    for sha, rows in grouped.items():
        conclusions = {row.get("conclusion") for row in rows}
        settled_at = max(str(row.get("updated_at") or "") for row in rows)
        if any(row.get("status") != "completed" for row in rows):
            state = "pending"
        elif conclusions & _FAILED:
            state = "red"
        elif conclusions <= _PASSED and "success" in conclusions:
            state = "green"
        else:
            state = "unknown"
        result[sha] = (state, settled_at)
    return result


class Observer:
    """Fetch complete snapshots, caching commit metadata until the PR refs change."""

    def __init__(
        self,
        owner: str,
        repo: str,
        number: int,
        out_dir: Path | None = None,
        *,
        github_token: str | None = None,
    ) -> None:
        self.repo = f"{owner}/{repo}"
        self.number = number
        self.client = GitHubRestClient(github_token or _github_token())
        self.path = out_dir / f"{owner}__{repo}__pr{number}.json" if out_dir else None
        self.epochs: list[Epoch] = []
        self.commits: list[Commit] = []
        self.state = ""
        self._refs: tuple[str, str] | None = None
        self._history: list[dict[str, Any]] = []

    def _get(self, path: str, **params: Any) -> Any:
        return self.client.request("GET", f"repos/{self.repo}/{path}", params=params)

    def _pages(self, path: str, key: str = "", **params: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, 101):
            payload = self._get(path, per_page=100, page=page, **params)
            batch = payload[key] if key else payload
            if not isinstance(batch, list):
                raise ValueError("GitHub returned an invalid history page.")
            rows.extend(batch)
            total = payload.get("total_count", payload.get("total_commits")) if key else None
            if len(batch) < 100:
                if total is not None and len(rows) < total:
                    raise ValueError("GitHub history is truncated; no epoch snapshot was written.")
                return rows
            if total is not None and len(rows) >= total:
                return rows
        raise ValueError("GitHub history exceeded the page limit; no epoch snapshot was written.")

    def _pr_runs(self, **params: Any) -> list[dict[str, Any]]:
        """Exclude other events and runs without an explicit link to this PR."""
        return [
            run
            for run in self._pages("actions/runs", "workflow_runs", event="pull_request", **params)
            if run.get("event") == "pull_request"
            and any(pr.get("number") == self.number for pr in run.get("pull_requests", []))
        ]

    def tick(self) -> bool:
        """Rebuild from remote history, including repairs completed before startup."""
        pr = self._get(f"pulls/{self.number}")
        refs = (pr["base"]["sha"], pr["head"]["sha"])
        if refs != self._refs:
            if pr["commits"] <= 250:
                history = self._pages(f"pulls/{self.number}/commits")
            else:
                history = self._pages(f"compare/{refs[0]}...{refs[1]}", "commits")
            if (
                len(history) != pr["commits"]
                or len({row["sha"] for row in history}) != len(history)
                or not history
                or history[-1]["sha"] != refs[1]
            ):
                raise ValueError("PR history changed or is incomplete; retry the snapshot.")
            self._history, self._refs = history, refs
        runs = self._pr_runs(branch=pr["head"]["ref"])
        covered = {run["head_sha"] for run in runs}
        for row in self._history:
            if row["sha"] not in covered:
                runs.extend(self._pr_runs(head_sha=row["sha"]))
        results = workflow_results(runs)
        self.commits = [
            Commit(row["sha"], commit_author(row), *results.get(row["sha"], ("unknown", "")))
            for row in self._history
        ]
        self.epochs = build_epochs(self.repo, self.number, self.commits)
        self.state = "merged" if pr.get("merged") else pr["state"]
        self.write()
        return self.state == "open"

    def write(self) -> None:
        if self.path is None:
            return
        snapshot = {
            "repository": self.repo,
            "pr_number": self.number,
            "state": self.state,
            "observed_at": datetime.now(UTC).isoformat(),
            "ci_source": "github_actions",
            "ci_coverage_complete": all(c.result in {"red", "green"} for c in self.commits),
            "attribution_source": "git_authorship",
            "commits": [asdict(commit) for commit in self.commits],
            "epochs": [epoch.record() for epoch in self.epochs],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        lines = [f"# {self.repo}#{self.number} ({self.state})", ""]
        for number, epoch in enumerate(self.epochs, 1):
            lines.append(f"Epoch {number}: {epoch.outcome} [{epoch.id[:12]}]")
            lines.extend(f"  {c.sha[:12]}  {c.result:7}  {c.author}" for c in epoch.commits)
            if fixing := epoch.fixing_commit:
                lines.append(f"  fixing_sha: {fixing.sha} ({fixing.author})")
            lines.append("")
        self.path.with_suffix(".txt").write_text(
            "\n".join(lines)
            if self.epochs
            else lines[0] + "\nNo red epoch in the available CI history.\n",
            encoding="utf-8",
        )

    def publish(self, *, fixing_sha: str | None = None) -> int:
        """Queue fixes; consumers deduplicate by repository, PR, and fixing SHA."""
        published = 0
        for epoch in self.epochs:
            fixing = epoch.fixing_commit
            if epoch.outcome != "agent_fixed" or fixing is None or not fixing.settled_at:
                continue
            if fixing_sha is not None and fixing.sha != fixing_sha:
                continue
            get_analytics().capture(
                Event.OPENSRE_CI_EPOCH_RESOLVED,
                {
                    "epoch_id": epoch.id,
                    "repository": self.repo,
                    "pr_number": self.number,
                    "first_red_sha": epoch.commits[0].sha,
                    "fixing_sha": fixing.sha,
                    "fixer": "opensre",
                    "outcome": epoch.outcome,
                    "ci_source": "github_actions",
                    "ci_updated_at": fixing.settled_at,
                    "attribution_source": "git_authorship",
                },
            )
            published += 1
        return published


def publish_repair_epoch(
    owner: str, repo: str, number: int, *, github_token: str | None, fixing_sha: str
) -> None:
    """Publish only this repair's epoch without letting analytics fail a repair."""
    try:
        observer = Observer(owner, repo, number, github_token=github_token)
        observer.tick()
        observer.publish(fixing_sha=fixing_sha)
    except Exception as exc:
        capture_exception(exc)


def _github_token() -> str:
    try:
        return configured_token()
    except ValueError:
        return subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr", help="owner/repo#number")
    parser.add_argument(
        "--once", action="store_true", help="Write one historical snapshot and exit"
    )
    parser.add_argument(
        "--publish", action="store_true", help="Publish confirmed agent fixes to analytics"
    )
    parser.add_argument("--red-interval", type=float, default=30)
    parser.add_argument("--green-interval", type=float, default=180)
    parser.add_argument("--max-hours", type=float, default=8)
    parser.add_argument("--out", type=Path, default=Path(".tmp/cicd_analytics"))
    args = parser.parse_args()
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#([1-9][0-9]*)", args.pr)
    if not match:
        parser.error("expected owner/repo#number")
    if min(args.red_interval, args.green_interval, args.max_hours) <= 0:
        parser.error("poll intervals and duration must be positive")
    observer = Observer(match[1], match[2], int(match[3]), args.out)
    deadline = time.monotonic() + args.max_hours * 3600
    try:
        while True:
            opened = observer.tick()
            if args.publish:
                print(f"Queued {observer.publish()} confirmed agent-fixed epochs.")
            if not opened or args.once or time.monotonic() >= deadline:
                break
            green = observer.commits[-1].result == "green"
            time.sleep(
                min(
                    args.green_interval if green else args.red_interval,
                    max(0, deadline - time.monotonic()),
                )
            )
    except KeyboardInterrupt:
        print("Stopped; the latest completed snapshot is retained.")
        return
    finally:
        if args.publish:
            shutdown_analytics(flush=True, timeout=15)
    assert observer.path is not None
    print(f"Wrote {observer.path.with_suffix('.json')} and {observer.path.with_suffix('.txt')}")


if __name__ == "__main__":
    main()
