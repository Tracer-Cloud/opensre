"""Build coding tasks from GitHub security and quality findings.

Dependabot alerts, code-scanning alerts, and Code Quality findings can usually
be remediated by code or dependency changes in the current checkout.
Secret-scanning alerts are refused: the alert may point at a real credential,
and safe remediation requires rotation outside the repository before any code
cleanup.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Literal

from infrastructure.safety.masking import MaskingPolicy, MaskingRules
from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.repo_scope import detect_git_remote_repo_scope
from integrations.github.tools.security_fix.errors import (
    ERR_ALERT_NOT_FOUND,
    ERR_FIX_ALREADY_OPEN,
    ERR_GITHUB_UNAVAILABLE,
    ERR_INVALID_INPUT,
    ERR_NO_AUTOFIXABLE_FINDING,
    ERR_UNSUPPORTED_ALERT_TYPE,
    GitHubSecurityFixError,
)
from integrations.github.tools.security_fix.local_rules import has_builtin_local_fix

AlertType = Literal[
    "auto",
    "dependabot",
    "code_scanning",
    "code_quality",
    "secret_scanning",
]
ResolvedAlertType = Literal[
    "dependabot",
    "code_scanning",
    "code_quality",
    "secret_scanning",
]

_ALERT_TYPE_ALIASES: dict[str, ResolvedAlertType | Literal["auto"]] = {
    "": "auto",
    "auto": "auto",
    "dependabot": "dependabot",
    "dependabot_alert": "dependabot",
    "dependabot-alert": "dependabot",
    "code_scanning": "code_scanning",
    "code-scanning": "code_scanning",
    "code scanning": "code_scanning",
    "code_quality": "code_quality",
    "code-quality": "code_quality",
    "code quality": "code_quality",
    "quality": "code_quality",
    "security quality": "code_quality",
    "security_and_quality": "code_quality",
    "security-and-quality": "code_quality",
    "security and quality": "code_quality",
    "secret_scanning": "secret_scanning",
    "secret-scanning": "secret_scanning",
    "secret scanning": "secret_scanning",
}
_SUPPORTED_AUTO_TYPES: tuple[ResolvedAlertType, ...] = (
    "dependabot",
    "code_scanning",
    "code_quality",
)
_EXACT_LOOKUP_TYPES: tuple[ResolvedAlertType, ...] = (
    "dependabot",
    "code_scanning",
    "code_quality",
    "secret_scanning",
)
_SEVERITY_RANK = {
    "critical": 5,
    "high": 4,
    "error": 4,
    "medium": 3,
    "warning": 2,
    "low": 1,
    "note": 0,
}
_MAX_TEXT_CHARS = 1200
_CODE_QUALITY_API_VERSION = "2026-03-10"

_SECURITY_URL_RE = re.compile(
    r"https?://github\.com/"
    r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+)/"
    r"(?P<section>security/(?P<security_kind>dependabot|code-scanning|secret-scanning)"
    r"|code-scanning(?:/alerts)?)"
    r"(?:/(?P<number>\d+))?/?"
    r"(?:[?#][^\s]*)?(?=$|[\s).,;])",
    re.IGNORECASE,
)
_QUALITY_URL_RE = re.compile(
    r"https?://github\.com/"
    r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+)/"
    r"(?:security/quality(?:/findings)?|code-quality/findings)"
    r"(?:/(?P<number>\d+))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SecurityAlertRef:
    """Repository and alert identity parsed from a GitHub alert URL."""

    owner: str
    repo: str
    alert_type: ResolvedAlertType
    number: int | None


@dataclass(frozen=True)
class SecurityAlertContext:
    """Resolved GitHub security or quality finding and coding-agent task."""

    owner: str
    repo: str
    alert_type: ResolvedAlertType
    number: int
    summary: str
    url: str
    task: str
    rule_id: str = ""
    location_path: str = ""
    start_line: int | None = None


def normalize_alert_type(alert_type: str | None) -> AlertType:
    """Return a normalized alert type or raise for unsupported input."""
    key = " ".join(str(alert_type or "").strip().lower().replace("-", "_").split())
    normalized = _ALERT_TYPE_ALIASES.get(key)
    if normalized is None:
        raise GitHubSecurityFixError(
            ERR_INVALID_INPUT,
            "alert_type must be auto, dependabot, code_scanning, code_quality, or secret_scanning.",
        )
    return normalized


def parse_security_alert_url(alert_url: str | None) -> SecurityAlertRef | None:
    """Parse common GitHub security and quality URLs."""
    if not alert_url:
        return None
    quality_match = _QUALITY_URL_RE.search(alert_url.strip())
    if quality_match is not None:
        raw_number = quality_match.group("number")
        return SecurityAlertRef(
            owner=quality_match.group("owner"),
            repo=quality_match.group("repo").removesuffix(".git"),
            alert_type="code_quality",
            number=int(raw_number) if raw_number else None,
        )

    match = _SECURITY_URL_RE.search(alert_url.strip())
    if match is None:
        return None

    security_kind = (match.group("security_kind") or "").lower()
    section = match.group("section").lower()
    if security_kind == "dependabot":
        alert_type: ResolvedAlertType = "dependabot"
    elif security_kind == "secret-scanning":
        alert_type = "secret_scanning"
    elif security_kind == "code-scanning" or section.startswith("code-scanning"):
        alert_type = "code_scanning"
    else:
        return None
    raw_number = match.group("number")
    return SecurityAlertRef(
        owner=match.group("owner"),
        repo=match.group("repo").removesuffix(".git"),
        alert_type=alert_type,
        number=int(raw_number) if raw_number else None,
    )


def gather_security_alert_context(
    *,
    owner: str | None = None,
    repo: str | None = None,
    alert_type: str | None = None,
    alert_number: int | None = None,
    alert_url: str | None = None,
    workspace: str | None = None,
    github_token: str | None = None,
    prefer_builtin_local_fix: bool = False,
    exclude: Collection[tuple[str, int]] = (),
) -> SecurityAlertContext:
    """Resolve a GitHub alert and build the coding-agent task.

    ``exclude`` lists ``(alert_type, number)`` pairs that automatic selection
    skips (findings that already have a fix in flight); an explicit number is
    never filtered.
    """
    parsed_url = parse_security_alert_url(alert_url)
    normalized_type = parsed_url.alert_type if parsed_url else normalize_alert_type(alert_type)
    number = parsed_url.number if parsed_url and parsed_url.number is not None else alert_number
    repo_owner = (parsed_url.owner if parsed_url else owner or "").strip()
    repo_name = (parsed_url.repo if parsed_url else repo or "").strip().removesuffix(".git")

    if not repo_owner or not repo_name:
        detected = detect_git_remote_repo_scope(workspace)
        if detected is not None:
            repo_owner, repo_name = detected
    if not repo_owner or not repo_name:
        raise GitHubSecurityFixError(
            ERR_INVALID_INPUT,
            "owner/repo is required unless alert_url or the workspace origin identifies a GitHub repo.",
        )

    if normalized_type == "secret_scanning":
        raise GitHubSecurityFixError(
            ERR_UNSUPPORTED_ALERT_TYPE,
            "Secret-scanning alerts are not auto-fixed: revoke or rotate the secret first, then remove it from history with a repo-specific plan.",
        )

    client = GitHubRestClient(github_token)
    if number is not None:
        return _get_exact_alert_context(
            client,
            owner=repo_owner,
            repo=repo_name,
            alert_type=normalized_type,
            number=int(number),
        )
    return _select_first_alert_context(
        client,
        owner=repo_owner,
        repo=repo_name,
        alert_type=normalized_type,
        prefer_builtin_local_fix=prefer_builtin_local_fix,
        exclude=exclude,
    )


def _get_exact_alert_context(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    alert_type: AlertType,
    number: int,
) -> SecurityAlertContext:
    if alert_type == "auto":
        selected_types: tuple[ResolvedAlertType, ...] = _EXACT_LOOKUP_TYPES
    else:
        selected_types = (alert_type,)
    not_found: list[str] = []
    for candidate in selected_types:
        if candidate == "secret_scanning":
            if alert_type == "secret_scanning":
                raise GitHubSecurityFixError(
                    ERR_UNSUPPORTED_ALERT_TYPE,
                    "Secret-scanning alerts are not auto-fixed because safe remediation requires secret rotation outside the repository.",
                )
            continue
        try:
            alert = _fetch_alert(
                client, owner=owner, repo=repo, alert_type=candidate, number=number
            )
        except GitHubApiError as exc:
            if exc.status_code == HTTPStatus.NOT_FOUND:
                not_found.append(candidate)
                continue
            raise _github_error(exc) from exc
        return _context_from_alert(
            client, owner=owner, repo=repo, alert_type=candidate, alert=alert
        )
    searched = ", ".join(not_found or [str(alert_type)])
    raise GitHubSecurityFixError(
        ERR_ALERT_NOT_FOUND,
        f"GitHub security alert #{number} was not found in {owner}/{repo} ({searched}).",
    )


def _select_first_alert_context(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    alert_type: AlertType,
    prefer_builtin_local_fix: bool,
    exclude: Collection[tuple[str, int]] = (),
) -> SecurityAlertContext:
    selected_types = _SUPPORTED_AUTO_TYPES if alert_type == "auto" else (alert_type,)
    excluded = set(exclude)
    candidates: list[tuple[int, bool, ResolvedAlertType, dict[str, Any]]] = []
    errors: list[str] = []
    for candidate in selected_types:
        if candidate == "secret_scanning":
            raise GitHubSecurityFixError(
                ERR_UNSUPPORTED_ALERT_TYPE,
                "Secret-scanning alerts are not auto-fixed because safe remediation requires secret rotation outside the repository.",
            )
        try:
            alerts = client.paginate(
                _alert_collection_path(owner, repo, candidate),
                params={"state": "open", "per_page": 100},
                api_version=_api_version(candidate),
            )
        except GitHubApiError as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        candidates.extend(
            (
                _alert_severity_rank(candidate, alert),
                _has_builtin_local_fix(candidate, alert),
                candidate,
                alert,
            )
            for alert in alerts
            if isinstance(alert.get("number"), int)
        )
    if not candidates:
        if errors:
            detail = f" Errors: {'; '.join(errors)}"
            raise GitHubSecurityFixError(
                ERR_GITHUB_UNAVAILABLE,
                f"Could not read GitHub security or quality findings in {owner}/{repo}; no PR was created.{detail}",
            )
        raise GitHubSecurityFixError(
            ERR_ALERT_NOT_FOUND,
            f"No open Dependabot, code-scanning, or Code Quality findings found in {owner}/{repo}; no PR was created.",
        )
    if excluded:
        remaining = [item for item in candidates if (item[2], item[3]["number"]) not in excluded]
        if not remaining:
            raise GitHubSecurityFixError(
                ERR_FIX_ALREADY_OPEN,
                f"Every open supported finding in {owner}/{repo} already has an OpenSRE fix pull request open; no new PR was created.",
            )
        candidates = remaining
    if prefer_builtin_local_fix:
        local_candidates = [item for item in candidates if item[1]]
        if not local_candidates:
            raise GitHubSecurityFixError(
                ERR_NO_AUTOFIXABLE_FINDING,
                f"Found open GitHub security or quality findings in {owner}/{repo}, but none match OpenSRE's built-in auto-fixers; no PR was created.",
            )
        candidates = local_candidates
    _rank, _local_supported, selected_type, selected = max(
        candidates,
        key=lambda item: (
            item[0],
            int(item[3].get("number") or 0),
        ),
    )
    return _context_from_alert(
        client,
        owner=owner,
        repo=repo,
        alert_type=selected_type,
        alert=selected,
    )


def _fetch_alert(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    alert_type: ResolvedAlertType,
    number: int,
) -> dict[str, Any]:
    payload = client.request(
        "GET",
        _alert_item_path(owner, repo, alert_type, number),
        api_version=_api_version(alert_type),
    )
    if not isinstance(payload, dict):
        raise GitHubSecurityFixError(
            ERR_ALERT_NOT_FOUND, "GitHub returned an unexpected alert payload."
        )
    return payload


def _context_from_alert(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    alert_type: ResolvedAlertType,
    alert: dict[str, Any],
) -> SecurityAlertContext:
    number = alert.get("number")
    if not isinstance(number, int):
        raise GitHubSecurityFixError(ERR_ALERT_NOT_FOUND, "GitHub alert payload has no number.")
    if alert_type == "dependabot":
        return _dependabot_context(owner=owner, repo=repo, alert=alert, number=number)
    if alert_type == "code_quality":
        return _code_quality_context(owner=owner, repo=repo, finding=alert, number=number)
    return _code_scanning_context(
        client,
        owner=owner,
        repo=repo,
        alert=alert,
        number=number,
    )


def _dependabot_context(
    *, owner: str, repo: str, alert: dict[str, Any], number: int
) -> SecurityAlertContext:
    dependency = _dict_value(alert.get("dependency"))
    package = _dict_value(dependency.get("package"))
    vulnerability = _dict_value(alert.get("security_vulnerability"))
    vulnerability_package = _dict_value(vulnerability.get("package"))
    advisory = _dict_value(alert.get("security_advisory"))
    patched = vulnerability.get("first_patched_version")
    patched_version = str(patched.get("identifier") or "") if isinstance(patched, dict) else ""
    manifest = str(dependency.get("manifest_path") or "").strip()
    package_name = str(package.get("name") or vulnerability_package.get("name") or "")
    ecosystem = str(package.get("ecosystem") or vulnerability_package.get("ecosystem") or "")
    vulnerable_range = str(vulnerability.get("vulnerable_version_range") or "")
    summary = str(advisory.get("summary") or f"Dependabot alert for {package_name}").strip()
    severity = str(vulnerability.get("severity") or advisory.get("severity") or "")
    identifiers_payload = advisory.get("identifiers")
    identifiers_list = identifiers_payload if isinstance(identifiers_payload, list) else []
    identifiers = ", ".join(
        str(item.get("value") or "")
        for item in identifiers_list
        if isinstance(item, dict) and item.get("value")
    )
    url = str(alert.get("html_url") or "")
    task = _masked_lines(
        [
            f"Fix GitHub Dependabot security alert #{number} in {owner}/{repo}.",
            "",
            f"Alert URL: {url}",
            f"Package: {package_name}",
            f"Ecosystem: {ecosystem}",
            f"Manifest: {manifest}",
            f"Severity: {severity}",
            f"Vulnerable range: {vulnerable_range}",
            f"First patched version: {patched_version}",
            f"Identifiers: {identifiers}",
            f"Summary: {summary}",
            "",
            "Make the minimal dependency update that satisfies the patched version.",
            "Update any lockfile that belongs to the manifest. Do not rewrite unrelated dependencies.",
            "Run the smallest relevant verification command when it is obvious from the repo.",
            "Explain the change and any compatibility risk.",
        ]
    )
    return SecurityAlertContext(
        owner=owner,
        repo=repo,
        alert_type="dependabot",
        number=number,
        summary=summary,
        url=url,
        task=task,
    )


def _code_quality_context(
    *, owner: str, repo: str, finding: dict[str, Any], number: int
) -> SecurityAlertContext:
    rule = _dict_value(finding.get("rule"))
    location = _dict_value(finding.get("location"))
    message = _dict_value(finding.get("message"))
    rule_id = str(rule.get("id") or "")
    title = str(rule.get("title") or rule_id or "Code quality finding").strip()
    description = str(rule.get("description") or "").strip()
    category = str(rule.get("category") or "")
    severity = str(rule.get("severity") or "")
    # The Code Quality findings API only returns an authenticated API ``url``
    # (``html_url`` is null today). Prefer a real ``html_url`` when present;
    # otherwise synthesize the public github.com finding page — never surface
    # ``api.github.com/...`` in tasks, PR bodies, or tool responses.
    url = _code_quality_public_url(owner=owner, repo=repo, number=number, finding=finding)
    summary = title
    task = _masked_lines(
        [
            f"Fix GitHub Code Quality finding #{number} in {owner}/{repo}.",
            "",
            f"Finding URL: {url}",
            f"Rule: {rule_id}",
            f"Title: {title}",
            f"Category: {category}",
            f"Severity: {severity}",
            f"Description: {_text(description)}",
            f"Help: {_text(rule.get('help'))}",
            f"Location: {_format_location(location)}",
            f"Message: {_text(message.get('text') or message.get('markdown'))}",
            "",
            "Make the smallest source change that resolves this Code Quality finding.",
            "Prefer direct cleanup over broad refactors; preserve behavior unless the finding is a genuine bug.",
            "For maintainability findings, keep the change narrow and idiomatic for this repo.",
            "For reliability findings, add or update the smallest relevant test when the behavior is testable.",
            "Explain the change and how it improves maintainability or reliability.",
        ]
    )
    return SecurityAlertContext(
        owner=owner,
        repo=repo,
        alert_type="code_quality",
        number=number,
        summary=summary,
        url=url,
        task=task,
        rule_id=rule_id,
        location_path=_location_path(location),
        start_line=_start_line(location),
    )


def _code_scanning_context(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    alert: dict[str, Any],
    number: int,
) -> SecurityAlertContext:
    rule = _dict_value(alert.get("rule"))
    instance = _dict_value(alert.get("most_recent_instance"))
    instances = _code_scanning_instances(client, owner=owner, repo=repo, number=number)
    location = _dict_value(instance.get("location"))
    message = _dict_value(instance.get("message"))
    rule_id = str(rule.get("id") or rule.get("name") or "")
    summary = str(rule.get("description") or rule.get("full_description") or rule_id).strip()
    severity = str(rule.get("security_severity_level") or rule.get("severity") or "")
    url = str(alert.get("html_url") or "")
    task_lines = [
        f"Fix GitHub code-scanning security alert #{number} in {owner}/{repo}.",
        "",
        f"Alert URL: {url}",
        f"Rule: {rule_id}",
        f"Severity: {severity}",
        f"Summary: {summary}",
        f"Help: {_text(rule.get('help') or rule.get('full_description'))}",
        f"Primary location: {_format_location(location)}",
        f"Primary message: {_text(message.get('text'))}",
    ]
    if instances:
        task_lines.extend(["", "Additional instances:"])
        task_lines.extend(f"- {_format_location(item.get('location'))}" for item in instances[:5])
    task_lines.extend(
        [
            "",
            "Make a minimal source change that removes the security finding.",
            "Do not dismiss or silence the alert unless the source fix makes the rule provably unnecessary.",
            "Add or update the smallest relevant test when the vulnerable behavior is testable.",
            "Explain the change and how it addresses the rule.",
        ]
    )
    return SecurityAlertContext(
        owner=owner,
        repo=repo,
        alert_type="code_scanning",
        number=number,
        summary=summary,
        url=url,
        task=_masked_lines(task_lines),
        rule_id=rule_id,
        location_path=_location_path(location),
        start_line=_start_line(location),
    )


def _code_scanning_instances(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    number: int,
) -> list[dict[str, Any]]:
    try:
        return client.paginate(
            f"/repos/{owner}/{repo}/code-scanning/alerts/{number}/instances",
            params={"per_page": 10},
        )
    except GitHubApiError:
        return []


def _alert_collection_path(owner: str, repo: str, alert_type: ResolvedAlertType) -> str:
    if alert_type == "dependabot":
        return f"/repos/{owner}/{repo}/dependabot/alerts"
    if alert_type == "code_scanning":
        return f"/repos/{owner}/{repo}/code-scanning/alerts"
    if alert_type == "code_quality":
        return f"/repos/{owner}/{repo}/code-quality/findings"
    return f"/repos/{owner}/{repo}/secret-scanning/alerts"


def _alert_item_path(owner: str, repo: str, alert_type: ResolvedAlertType, number: int) -> str:
    if alert_type == "dependabot":
        return f"/repos/{owner}/{repo}/dependabot/alerts/{number}"
    if alert_type == "code_scanning":
        return f"/repos/{owner}/{repo}/code-scanning/alerts/{number}"
    if alert_type == "code_quality":
        return f"/repos/{owner}/{repo}/code-quality/findings/{number}"
    return f"/repos/{owner}/{repo}/secret-scanning/alerts/{number}"


def _api_version(alert_type: ResolvedAlertType) -> str:
    if alert_type == "code_quality":
        return _CODE_QUALITY_API_VERSION
    return "2022-11-28"


def _alert_severity_rank(alert_type: ResolvedAlertType, alert: dict[str, Any]) -> int:
    if alert_type == "dependabot":
        vulnerability = alert.get("security_vulnerability")
        advisory = alert.get("security_advisory")
        severity = ""
        if isinstance(vulnerability, dict):
            severity = str(vulnerability.get("severity") or "")
        if not severity and isinstance(advisory, dict):
            severity = str(advisory.get("severity") or "")
        return _SEVERITY_RANK.get(severity.lower(), 0)
    rule = alert.get("rule")
    severity = ""
    if isinstance(rule, dict):
        severity = str(rule.get("security_severity_level") or rule.get("severity") or "")
    return _SEVERITY_RANK.get(severity.lower(), 0)


def _has_builtin_local_fix(alert_type: ResolvedAlertType, alert: dict[str, Any]) -> bool:
    rule = _dict_value(alert.get("rule"))
    return has_builtin_local_fix(
        alert_type=alert_type,
        rule_id=str(rule.get("id") or ""),
        title=str(rule.get("title") or ""),
    )


def _github_error(exc: GitHubApiError) -> GitHubSecurityFixError:
    kind = (
        ERR_ALERT_NOT_FOUND if exc.status_code == HTTPStatus.NOT_FOUND else ERR_GITHUB_UNAVAILABLE
    )
    return GitHubSecurityFixError(kind, str(exc))


def _code_quality_public_url(*, owner: str, repo: str, number: int, finding: dict[str, Any]) -> str:
    """Return a github.com URL for a Code Quality finding (never api.github.com)."""
    html_url = str(finding.get("html_url") or "").strip()
    if html_url.startswith("https://github.com/"):
        return html_url
    return f"https://github.com/{owner}/{repo}/security/quality/findings/{number}"


def _format_location(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    path = str(value.get("path") or "")
    start = value.get("start_line")
    end = value.get("end_line")
    if isinstance(start, int) and isinstance(end, int) and end != start:
        return f"{path}:{start}-{end}"
    if isinstance(start, int):
        return f"{path}:{start}"
    return path


def _location_path(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("path") or "")


def _start_line(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    start = value.get("start_line")
    return start if isinstance(start, int) else None


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()[:_MAX_TEXT_CHARS]


def _masked_lines(lines: list[str]) -> str:
    masker = MaskingRules(MaskingPolicy.from_env())
    return "\n".join(masker.mask(_text(line)) for line in lines if line.strip() or line == "")


__all__ = [
    "AlertType",
    "SecurityAlertContext",
    "SecurityAlertRef",
    "gather_security_alert_context",
    "normalize_alert_type",
    "parse_security_alert_url",
]
