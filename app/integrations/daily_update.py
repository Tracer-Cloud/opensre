"""Generate and post a daily OpenSRE update from GitHub activity."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib import error, parse, request
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.services.llm_client import get_llm_for_reasoning
from app.version import get_version

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
LONDON_TZ = ZoneInfo("Europe/London")
DEFAULT_OUTPUT_DIR = "docs/daily-updates"
MAX_PROMPT_FILES = 25
MAX_PROMPT_BODY_CHARS = 1200
MAX_HIGHLIGHTS = 20
BOT_LOGINS = frozenset({
    "dependabot",
    "dependabot[bot]",
    "github-actions",
    "github-actions[bot]",
    "copilot",
    "copilot[bot]",
})


@dataclass(frozen=True, slots=True)
class Contributor:
    """Human contributor associated with a merged PR."""

    login: str
    display_name: str


@dataclass(frozen=True, slots=True)
class PullRequestSummary:
    """Normalized GitHub pull request data used for the daily update."""

    number: int
    title: str
    url: str
    author_login: str
    author_display_name: str
    merged_at: datetime
    body: str
    labels: tuple[str, ...]
    changed_files: tuple[str, ...]
    additions: int
    deletions: int
    contributors: tuple[Contributor, ...]


@dataclass(frozen=True, slots=True)
class DailyWindow:
    """London-local day with matching UTC boundaries for API filtering."""

    london_date: date
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True, slots=True)
class DailyUpdate:
    """Rendered daily summary plus source data."""

    title: str
    thanks_line: str
    highlights: tuple[str, ...]
    window: DailyWindow
    pull_requests: tuple[PullRequestSummary, ...]
    fallback_used: bool


class HighlightResponse(BaseModel):
    """Structured highlight bullets produced by the LLM summarizer."""

    highlights: list[str] = Field(min_length=1, max_length=MAX_HIGHLIGHTS)


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _bool_env(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"", "0", "false", "no"}


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"Expected timezone-aware datetime, got {value!r}")
    return parsed.astimezone(UTC)


def compute_daily_window(*, now: datetime | None = None, london_date: date | None = None) -> DailyWindow:
    """Return the London calendar day and UTC bounds used for GitHub queries."""
    if london_date is None:
        now_utc = now or datetime.now(UTC)
        if now_utc.tzinfo is None:
            raise ValueError("compute_daily_window requires a timezone-aware datetime.")
        london_date = now_utc.astimezone(LONDON_TZ).date()

    local_start = datetime.combine(london_date, time.min, tzinfo=LONDON_TZ)
    local_end = local_start + timedelta(days=1)
    return DailyWindow(
        london_date=london_date,
        start_utc=local_start.astimezone(UTC),
        end_utc=local_end.astimezone(UTC),
    )


def _resolve_target_window() -> DailyWindow:
    override_date = _string(os.getenv("DAILY_UPDATE_DATE"))
    if override_date:
        return compute_daily_window(london_date=date.fromisoformat(override_date))

    override_now = _string(os.getenv("DAILY_UPDATE_NOW"))
    if override_now:
        return compute_daily_window(now=_parse_iso_datetime(override_now))

    return compute_daily_window()


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "opensre-daily-update",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def _github_json(url: str, token: str) -> tuple[Any, Any]:
    req = request.Request(url, headers=_github_headers(token), method="GET")
    try:
        with request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload, response.headers
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API request failed with HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"GitHub API request failed: {exc.reason}") from exc


def _next_link(headers: Any) -> str | None:
    link_header = headers.get("Link", "")
    for part in link_header.split(","):
        if 'rel="next"' not in part:
            continue
        match = re.search(r"<([^>]+)>", part)
        if match:
            return match.group(1)
    return None


def _paginate_github(url: str, token: str) -> list[Any]:
    items: list[Any] = []
    next_url: str | None = url
    while next_url:
        payload, headers = _github_json(next_url, token)
        if not isinstance(payload, list):
            raise RuntimeError(f"Expected list payload from GitHub API, got {type(payload).__name__}.")
        items.extend(payload)
        next_url = _next_link(headers)
    return items


def _github_repo_api_url(repository: str, suffix: str) -> str:
    owner, separator, repo = repository.partition("/")
    if not owner or separator != "/" or not repo:
        raise ValueError(f"Expected GITHUB_REPOSITORY in owner/repo format, got {repository!r}")

    quoted_owner = parse.quote(owner, safe="")
    quoted_repo = parse.quote(repo, safe="")
    return f"{GITHUB_API_BASE_URL}/repos/{quoted_owner}/{quoted_repo}/{suffix.lstrip('/')}"


def _user_is_bot(user: dict[str, Any] | None) -> bool:
    if not isinstance(user, dict):
        return False
    login = _string(user.get("login")).lower()
    if not login:
        return False
    return login in BOT_LOGINS or login.endswith("[bot]") or _string(user.get("type")).lower() == "bot"


def _name_looks_like_bot(name: str) -> bool:
    lowered = name.strip().lower()
    return (
        lowered.endswith("[bot]")
        or lowered.endswith(" bot")
        or "github action" in lowered
        or "github-action" in lowered
        or "contrib-readme-action" in lowered
    )


def _resolve_user_display_name(login: str, token: str, cache: dict[str, str]) -> str:
    cached = cache.get(login.lower())
    if cached is not None:
        return cached

    url = f"{GITHUB_API_BASE_URL}/users/{parse.quote(login)}"
    try:
        payload, _headers = _github_json(url, token)
    except RuntimeError:
        cache[login.lower()] = login
        return login

    if isinstance(payload, dict):
        display_name = _string(payload.get("name")) or login
    else:
        display_name = login
    cache[login.lower()] = display_name
    return display_name


def _build_contributors(
    author_user: dict[str, Any] | None,
    commits: list[Any],
    token: str,
    user_cache: dict[str, str],
) -> tuple[Contributor, ...]:
    contributors: dict[str, Contributor] = {}

    def add_login(login: str) -> None:
        normalized = login.strip()
        if not normalized:
            return
        key = f"login:{normalized.lower()}"
        contributors[key] = Contributor(
            login=normalized,
            display_name=_resolve_user_display_name(normalized, token, user_cache),
        )

    def add_name(name: str) -> None:
        normalized = name.strip()
        if not normalized or _name_looks_like_bot(normalized):
            return
        key = f"name:{normalized.lower()}"
        contributors[key] = Contributor(login="", display_name=normalized)

    if isinstance(author_user, dict) and not _user_is_bot(author_user):
        add_login(_string(author_user.get("login")))

    for commit_obj in commits:
        if not isinstance(commit_obj, dict):
            continue

        author_user_obj = commit_obj.get("author")
        if isinstance(author_user_obj, dict) and not _user_is_bot(author_user_obj):
            add_login(_string(author_user_obj.get("login")))
        else:
            commit_author = commit_obj.get("commit")
            if isinstance(commit_author, dict):
                author_payload = commit_author.get("author")
                if isinstance(author_payload, dict):
                    add_name(_string(author_payload.get("name")))

    return tuple(sorted(contributors.values(), key=lambda item: item.display_name.lower()))


def fetch_merged_pull_requests(repository: str, window: DailyWindow, token: str) -> tuple[PullRequestSummary, ...]:
    """Fetch merged PRs for a single London-local day."""
    closed_prs_url = _github_repo_api_url(
        repository,
        "pulls?state=closed&sort=updated&direction=desc&per_page=100",
    )
    stub_pages = _paginate_github(closed_prs_url, token)
    user_cache: dict[str, str] = {}
    results: list[PullRequestSummary] = []

    for stub in stub_pages:
        if not isinstance(stub, dict):
            continue
        updated_at_raw = _string(stub.get("updated_at"))
        if updated_at_raw and _parse_iso_datetime(updated_at_raw) < window.start_utc:
            break

        merged_at_raw = _string(stub.get("merged_at"))
        if not merged_at_raw:
            continue

        merged_at = _parse_iso_datetime(merged_at_raw)
        if not (window.start_utc <= merged_at < window.end_utc):
            continue

        number = stub.get("number")
        if not isinstance(number, int):
            continue

        detail_url = _github_repo_api_url(repository, f"pulls/{number}")
        files_url = _github_repo_api_url(repository, f"pulls/{number}/files?per_page=100")
        commits_url = _github_repo_api_url(repository, f"pulls/{number}/commits?per_page=100")

        detail_payload, _detail_headers = _github_json(detail_url, token)
        file_payloads = _paginate_github(files_url, token)
        commit_payloads = _paginate_github(commits_url, token)

        if not isinstance(detail_payload, dict):
            raise RuntimeError(f"Expected pull request detail payload for #{number}.")

        author_user = detail_payload.get("user")
        author_login = ""
        author_display_name = "unknown"
        if isinstance(author_user, dict):
            author_login = _string(author_user.get("login"))
            if author_login:
                author_display_name = _resolve_user_display_name(author_login, token, user_cache)

        labels_payload = detail_payload.get("labels")
        labels = tuple(
            sorted(
                _string(label.get("name"))
                for label in labels_payload
                if isinstance(label, dict) and _string(label.get("name"))
            )
        ) if isinstance(labels_payload, list) else ()

        changed_files = tuple(
            _string(file_payload.get("filename"))
            for file_payload in file_payloads
            if isinstance(file_payload, dict) and _string(file_payload.get("filename"))
        )

        contributors = _build_contributors(author_user if isinstance(author_user, dict) else None, commit_payloads, token, user_cache)
        results.append(
            PullRequestSummary(
                number=number,
                title=_string(detail_payload.get("title")) or f"Pull request #{number}",
                url=_string(detail_payload.get("html_url")),
                author_login=author_login,
                author_display_name=author_display_name,
                merged_at=merged_at,
                body=_string(detail_payload.get("body")),
                labels=labels,
                changed_files=changed_files,
                additions=int(detail_payload.get("additions") or 0),
                deletions=int(detail_payload.get("deletions") or 0),
                contributors=contributors,
            )
        )

    return tuple(sorted(results, key=lambda pr: (pr.merged_at, pr.number)))


def format_name_list(names: Iterable[str]) -> str:
    """Render names with an Oxford comma for human-friendly thanks lines."""
    values = [name.strip() for name in names if name.strip()]
    if not values:
        return "no human contributors recorded in merged PRs today"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _thanks_line(pull_requests: tuple[PullRequestSummary, ...]) -> str:
    contributors: dict[str, str] = {}
    for pull_request in pull_requests:
        for contributor in pull_request.contributors:
            key = contributor.login.lower() if contributor.login else contributor.display_name.lower()
            contributors[key] = contributor.display_name
    return (
        "Thanks to everyone who contributed yesterday:\n\n"
        f"{format_name_list(sorted(contributors.values(), key=str.lower))} \U0001f64f\U0001f680"
    )


def _truncate(value: str, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", value.strip())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _prompt_file_list(changed_files: tuple[str, ...]) -> str:
    if not changed_files:
        return "None listed"
    files = list(changed_files[:MAX_PROMPT_FILES])
    suffix = ""
    if len(changed_files) > MAX_PROMPT_FILES:
        suffix = f", and {len(changed_files) - MAX_PROMPT_FILES} more"
    return ", ".join(files) + suffix


def _build_summary_prompt(repository: str, window: DailyWindow, pull_requests: tuple[PullRequestSummary, ...]) -> str:
    sections: list[str] = [
        "You are writing a factual internal engineering daily update.",
        f"Repository: {repository}",
        f"Date: {window.london_date.isoformat()} Europe/London",
        "",
        "Rules:",
        "- Select the top 20 most impactful merged pull requests from the list below.",
        "- Prioritize contributor diversity: include at least one PR per unique contributor before adding a second from the same contributor.",
        "- Format each highlight as a single line: <PR title> (#<number>) \u2014 <author>",
        "- If the PR title already contains (#<number>), do NOT add it again; just append \u2014 <author>.",
        "- Keep the original PR title as-is. Do not rewrite, editorialize, or group titles.",
        "- Use the author display name (not login) when available.",
        "- Exclude bot-authored PRs (dependabot, github-actions, contrib-readme-action).",
        "- Return up to 20 highlights, ordered by significance/impact.",
        "",
        "Source pull requests:",
    ]

    for pull_request in pull_requests:
        labels = ", ".join(pull_request.labels) or "none"
        contributors = ", ".join(contributor.display_name for contributor in pull_request.contributors) or "unknown"
        sections.extend([
            f"- Title: {pull_request.title}",
            f"  Author: {pull_request.author_display_name or pull_request.author_login or 'unknown'}",
            f"  Contributors: {contributors}",
            f"  Merged at: {pull_request.merged_at.isoformat()}",
            f"  Labels: {labels}",
            f"  Additions/Deletions: +{pull_request.additions} / -{pull_request.deletions}",
            f"  Files: {_prompt_file_list(pull_request.changed_files)}",
            f"  Body: {_truncate(pull_request.body, limit=MAX_PROMPT_BODY_CHARS) or 'No body provided.'}",
            "",
        ])

    return "\n".join(sections).strip()


def _format_pr_highlight(pr: PullRequestSummary) -> str:
    """Format a single PR as a highlight line: title (#number) — author."""
    author = pr.author_display_name or pr.author_login or "unknown"
    title = pr.title.strip()
    if f"#{pr.number}" in title:
        return f"{title} \u2014 {author}"
    return f"{title} (#{pr.number}) \u2014 {author}"


def build_fallback_highlights(pull_requests: tuple[PullRequestSummary, ...]) -> tuple[str, ...]:
    """Fallback to deterministic PR-title bullets when LLM summarization is unavailable."""
    if not pull_requests:
        return ("No pull requests were merged into `main` today.",)

    seen_authors: set[str] = set()
    primary: list[PullRequestSummary] = []
    secondary: list[PullRequestSummary] = []

    for pr in pull_requests:
        key = pr.author_login.lower() if pr.author_login else pr.author_display_name.lower()
        if key not in seen_authors:
            seen_authors.add(key)
            primary.append(pr)
        else:
            secondary.append(pr)

    ordered = (primary + secondary)[:MAX_HIGHLIGHTS]
    highlights = [_format_pr_highlight(pr) for pr in ordered]
    remaining = len(pull_requests) - len(ordered)
    if remaining > 0:
        highlights.append(f"{remaining} additional merged pull requests shipped.")
    return tuple(highlights)


def summarize_highlights(
    repository: str,
    window: DailyWindow,
    pull_requests: tuple[PullRequestSummary, ...],
) -> tuple[tuple[str, ...], bool]:
    """Summarize merged PRs with the configured reasoning model, or fall back safely."""
    if not pull_requests:
        return build_fallback_highlights(pull_requests), True

    prompt = _build_summary_prompt(repository, window, pull_requests)
    try:
        response = get_llm_for_reasoning().with_structured_output(HighlightResponse).invoke(prompt)
        highlights = tuple(item.strip() for item in response.highlights if item.strip())
        if highlights:
            return highlights, False
    except Exception:
        if _bool_env("DAILY_UPDATE_REQUIRE_LLM", default=False):
            raise

    return build_fallback_highlights(pull_requests), True


def build_daily_update(repository: str, window: DailyWindow, pull_requests: tuple[PullRequestSummary, ...]) -> DailyUpdate:
    """Create the normalized daily update document and Slack summary."""
    highlights, fallback_used = summarize_highlights(repository, window, pull_requests)
    repo_name = repository.rsplit("/", 1)[-1].lower()
    return DailyUpdate(
        title=f"Daily {repo_name} update",
        thanks_line=_thanks_line(pull_requests),
        highlights=highlights,
        window=window,
        pull_requests=pull_requests,
        fallback_used=fallback_used,
    )


def render_markdown(update: DailyUpdate) -> str:
    """Render a committed MDX archive document for docs/daily-updates."""
    london_date = update.window.london_date.isoformat()
    ld = update.window.london_date
    human_date = f"{ld:%B} {ld.day}, {ld:%Y}"
    lines = [
        "---",
        f'title: "Daily Update \u2014 {london_date}"',
        f'description: "OpenSRE engineering daily update for {london_date} (Europe/London)"',
        "---",
        "",
        update.thanks_line,
        "",
        f"## Main updates shipped ({human_date})",
        "",
    ]
    lines.extend(f"- {highlight}" for highlight in update.highlights)
    lines.extend([
        "",
        "## Source pull requests",
        "",
    ])

    if update.pull_requests:
        for pull_request in update.pull_requests:
            contributor_names = format_name_list(contributor.display_name for contributor in pull_request.contributors)
            files = ", ".join(f"`{path}`" for path in pull_request.changed_files[:10]) or "_No file list returned._"
            if len(pull_request.changed_files) > 10:
                files += f", and {len(pull_request.changed_files) - 10} more"
            labels = ", ".join(f"`{label}`" for label in pull_request.labels) or "_none_"
            lines.append(
                f"- [#{pull_request.number}]({pull_request.url}) {pull_request.title} "
                f"(author: {pull_request.author_display_name or pull_request.author_login or 'unknown'}; "
                f"contributors: {contributor_names}; labels: {labels}; files: {files})"
            )
    else:
        lines.append("- No pull requests were merged during this London calendar day.")

    lines.extend([
        "",
        "## Generation metadata",
        "",
        f"- Generator version: `opensre {get_version()}`",
        f"- Fallback summary used: `{'yes' if update.fallback_used else 'no'}`",
        f"- UTC window: `{update.window.start_utc.isoformat()}` to `{update.window.end_utc.isoformat()}`",
        "",
    ])
    return "\n".join(lines)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _docs_json_path() -> Path:
    return _repo_root() / "docs" / "docs.json"


def _output_dir() -> Path:
    configured = Path(_string(os.getenv("DAILY_UPDATE_OUTPUT_DIR")) or DEFAULT_OUTPUT_DIR)
    if configured.is_absolute():
        return configured
    return _repo_root() / configured


def update_docs_navigation(output_dir: Path) -> Path:
    """Add all daily-update pages to the Mintlify docs.json navigation."""
    docs_json = _docs_json_path()
    if not docs_json.exists():
        return docs_json

    config = json.loads(docs_json.read_text(encoding="utf-8"))

    archive_slugs = sorted(
        (
            f"daily-updates/{p.stem}"
            for p in output_dir.glob("*.mdx")
            if p.name != "overview.mdx"
        ),
        reverse=True,
    )

    pages: list[str] = ["daily-updates/overview", *archive_slugs]

    for group in config.get("navigation", {}).get("groups", []):
        if group.get("group") == "Daily Updates":
            group["pages"] = pages
            break

    docs_json.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return docs_json


def regenerate_overview(output_dir: Path) -> Path:
    """Rebuild the overview page listing all daily updates in reverse chronological order."""
    overview_path = output_dir / "overview.mdx"
    archive_files = sorted(
        (p for p in output_dir.glob("*.mdx") if p.name != "overview.mdx"),
        key=lambda p: p.stem,
        reverse=True,
    )

    lines = [
        "---",
        'title: "Daily Updates"',
        'description: "Automatically generated daily engineering updates from merged pull requests"',
        "---",
        "",
        "Daily updates are generated each evening (Europe/London) from the pull requests merged that day.",
        "",
        "| Date | Link |",
        "| ---- | ---- |",
    ]
    for archive_file in archive_files:
        slug = f"daily-updates/{archive_file.stem}"
        lines.append(f"| {archive_file.stem} | [{archive_file.stem}](/{slug}) |")

    if not archive_files:
        lines.append("| — | No daily updates yet. |")

    lines.append("")
    overview_path.write_text("\n".join(lines), encoding="utf-8")
    return overview_path


def write_daily_archive(update: DailyUpdate, *, output_dir: Path | None = None) -> Path:
    """Persist the generated MDX archive under docs/daily-updates."""
    target_dir = output_dir or _output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / f"{update.window.london_date.isoformat()}.mdx"
    archive_path.write_text(render_markdown(update), encoding="utf-8")
    regenerate_overview(target_dir)
    update_docs_navigation(target_dir)
    return archive_path


def _append_github_output(name: str, value: str) -> None:
    output_path = _string(os.getenv("GITHUB_OUTPUT"))
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main() -> int:
    """Entrypoint used by the scheduled GitHub Actions workflow."""
    repository = _string(os.getenv("GITHUB_REPOSITORY"))
    token = _string(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"))
    if not repository:
        print("Missing GITHUB_REPOSITORY.", file=sys.stderr)
        return 1
    if not token:
        print("Missing GITHUB_TOKEN or GH_TOKEN.", file=sys.stderr)
        return 1

    window = _resolve_target_window()
    pull_requests = fetch_merged_pull_requests(repository, window, token)
    update = build_daily_update(repository, window, pull_requests)
    archive_path = write_daily_archive(update)

    relative_archive_path = archive_path.relative_to(_repo_root()).as_posix()
    overview_path = archive_path.parent / "overview.mdx"
    relative_overview_path = overview_path.relative_to(_repo_root()).as_posix()
    docs_json = _docs_json_path()
    relative_docs_json = docs_json.relative_to(_repo_root()).as_posix()
    _append_github_output("archive_path", relative_archive_path)
    _append_github_output("overview_path", relative_overview_path)
    _append_github_output("docs_json_path", relative_docs_json)
    _append_github_output("used_fallback", "true" if update.fallback_used else "false")
    _append_github_output("london_date", update.window.london_date.isoformat())

    print(f"Wrote daily update archive to {relative_archive_path}")
    print(f"Regenerated overview at {relative_overview_path}")
    print(f"Updated docs navigation at {relative_docs_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
