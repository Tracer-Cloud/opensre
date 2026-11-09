"""Which CI a checkout runs on, and what its GitHub Actions workflows leave unset."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import yaml


class CiProvider(NamedTuple):
    """A CI service, recognized by the file or folder it reads from a checkout."""

    slug: str
    name: str
    marker: str


GITHUB_ACTIONS = CiProvider("github_actions", "GitHub Actions", ".github/workflows")
_PROVIDERS: tuple[CiProvider, ...] = (
    GITHUB_ACTIONS,
    CiProvider("gitlab_ci", "GitLab CI", ".gitlab-ci.yml"),
    CiProvider("circleci", "CircleCI", ".circleci/config.yml"),
    CiProvider("jenkins", "Jenkins", "Jenkinsfile"),
    CiProvider("buildkite", "Buildkite", ".buildkite"),
    CiProvider("azure_pipelines", "Azure Pipelines", "azure-pipelines.yml"),
    CiProvider("bitbucket_pipelines", "Bitbucket Pipelines", "bitbucket-pipelines.yml"),
    CiProvider("travis_ci", "Travis CI", ".travis.yml"),
    CiProvider("drone", "Drone", ".drone.yml"),
    CiProvider("woodpecker", "Woodpecker", ".woodpecker.yml"),
    CiProvider("teamcity", "TeamCity", ".teamcity"),
    CiProvider("cloud_build", "Google Cloud Build", "cloudbuild.yaml"),
    CiProvider("codebuild", "AWS CodeBuild", "buildspec.yml"),
)
_WORKFLOW_GLOBS = ("*.yml", "*.yaml")
_SHA_PINNED = re.compile(r"@[0-9a-f]{40}$")
# Versions GitHub has shut down: steps that use them fail.
_RETIRED = re.compile(r"^(actions/(?:upload|download)-artifact@v[123]|actions/cache@v[12])(?:\.|$)")
_CODE_TRIGGERS = frozenset({"push", "pull_request", "pull_request_target"})


@dataclass(frozen=True)
class WorkflowAudit:
    """What one checkout's GitHub Actions workflow files set and leave unset."""

    files: int = 0
    unreadable: int = 0
    jobs: int = 0
    jobs_without_timeout: int = 0
    """Jobs with no ``timeout-minutes``; GitHub then stops them after 6 hours."""

    action_refs: int = 0
    unpinned_action_refs: int = 0
    """``uses:`` references to a tag or branch rather than a full commit SHA."""

    retired_action_refs: tuple[str, ...] = ()
    """Retired action versions in use, as ``owner/action@vN``."""

    runs_without_concurrency: int = 0
    """Workflows run on pushes or pull requests with no concurrency group anywhere."""


def ci_providers(checkout: Path) -> tuple[CiProvider, ...]:
    """The CI services ``checkout`` has configuration for, GitHub Actions first."""
    found: list[CiProvider] = []
    for provider in _PROVIDERS:
        if provider is GITHUB_ACTIONS:
            if _workflow_files(checkout):
                found.append(provider)
        elif (checkout / provider.marker).exists():
            found.append(provider)
    return tuple(found)


def audit_workflows(checkout: Path) -> WorkflowAudit:
    """Timeouts, pinning, retired actions and concurrency across ``checkout``'s workflows."""
    files = _workflow_files(checkout)
    unreadable = jobs = without_timeout = refs = unpinned = without_concurrency = 0
    retired: set[str] = set()
    for path in files:
        workflow = _load(path)
        if workflow is None:
            unreadable += 1
            continue
        job_specs = _jobs(workflow)
        for job in job_specs:
            if "uses" not in job:
                jobs += 1
                if "timeout-minutes" not in job:
                    without_timeout += 1
        for ref in _action_refs(job_specs):
            refs += 1
            if not _SHA_PINNED.search(ref):
                unpinned += 1
            match = _RETIRED.match(ref)
            if match is not None:
                retired.add(match.group(1))
        if _runs_on_code_changes(workflow) and not _has_concurrency(workflow, job_specs):
            without_concurrency += 1
    return WorkflowAudit(
        files=len(files),
        unreadable=unreadable,
        jobs=jobs,
        jobs_without_timeout=without_timeout,
        action_refs=refs,
        unpinned_action_refs=unpinned,
        retired_action_refs=tuple(sorted(retired)),
        runs_without_concurrency=without_concurrency,
    )


def _workflow_files(checkout: Path) -> list[Path]:
    folder = checkout / GITHUB_ACTIONS.marker
    return sorted(
        path for pattern in _WORKFLOW_GLOBS for path in folder.glob(pattern) if path.is_file()
    )


def _load(path: Path) -> Mapping[Any, Any] | None:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError):
        return None
    return document if isinstance(document, Mapping) else None


def _jobs(workflow: Mapping[Any, Any]) -> list[Mapping[Any, Any]]:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, Mapping):
        return []
    return [job for job in jobs.values() if isinstance(job, Mapping)]


def _action_refs(jobs: list[Mapping[Any, Any]]) -> Iterator[str]:
    """Every ``uses:`` of a job or step that names a published action or reusable workflow."""
    for job in jobs:
        candidates: list[Any] = [job.get("uses")]
        steps = job.get("steps")
        if isinstance(steps, list):
            candidates.extend(step.get("uses") for step in steps if isinstance(step, Mapping))
        for ref in candidates:
            if isinstance(ref, str) and ref and not ref.startswith(("./", "docker://")):
                yield ref.strip()


def _runs_on_code_changes(workflow: Mapping[Any, Any]) -> bool:
    # YAML 1.1 reads a bare ``on`` key as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    if isinstance(triggers, str):
        return triggers in _CODE_TRIGGERS
    if isinstance(triggers, list):
        return any(trigger in _CODE_TRIGGERS for trigger in triggers)
    if isinstance(triggers, Mapping):
        return any(trigger in _CODE_TRIGGERS for trigger in triggers)
    return False


def _has_concurrency(workflow: Mapping[Any, Any], jobs: list[Mapping[Any, Any]]) -> bool:
    return "concurrency" in workflow or any("concurrency" in job for job in jobs)


__all__ = ["GITHUB_ACTIONS", "CiProvider", "WorkflowAudit", "audit_workflows", "ci_providers"]
