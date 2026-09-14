"""Resolve bundled Markdown resources and compose local includes and report templates."""

from __future__ import annotations

from pathlib import Path

from config.constants.skills import SKILL_FILENAME, SKILL_REPORT_SUFFIX

_REPO_SKILLS_PREFIX = "core/agent_harness/prompts/skills"
_REPORT_TEMPLATE_HEADER = "REPORT TEMPLATE from `{repo_path}` (fill exactly; keep all headings):"
_REFERENCE_HEADER = "SHARED RULES from `{repo_path}`:"


def skills_dir() -> Path:
    """Return the directory that holds the bundled skill markdown files."""
    return Path(__file__).parents[1]


def _repo_relative_path(path: Path) -> str:
    """Return a stable repo-relative path for prompt references."""
    try:
        relative = path.relative_to(skills_dir())
    except ValueError:
        return path.name
    return f"{_REPO_SKILLS_PREFIX}/{relative.as_posix()}"


def _report_template_path(skill_path: Path) -> Path:
    """Return the sibling report template path for a skill recipe."""
    package_name = skill_path.parent.name
    if skill_path.name == SKILL_FILENAME:
        return skill_path.parent / f"{package_name}{SKILL_REPORT_SUFFIX}"
    return skill_path.with_name(f"{skill_path.stem}{SKILL_REPORT_SUFFIX}")


def _path_is_under(path: Path, root: Path) -> bool:
    """Return True when ``path`` is ``root`` or a file inside it."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_skill_include(skill_path: Path, ref: str) -> Path | None:
    """Resolve one ``includes:`` entry to a markdown file under the skills tree."""
    name = ref.strip()
    if not name:
        return None
    relative = Path(name)
    if relative.is_absolute():
        return None
    root = skills_dir().resolve()
    seen: set[Path] = set()
    for raw_candidate in (
        skill_path.parent / relative,
        skill_path.parent.parent / relative,
        root / relative,
    ):
        try:
            candidate = raw_candidate.resolve()
        except OSError:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        if not _path_is_under(candidate, root):
            continue
        if candidate == skill_path.resolve():
            continue
        if candidate.name == SKILL_FILENAME:
            continue
        if candidate.is_file() and candidate.suffix.lower() == ".md":
            return candidate
    return None


def append_skill_includes(skill_path: Path, body: str, includes: tuple[str, ...]) -> str:
    """Append each readable in-tree include once, preserving declaration order."""
    if not body or not includes:
        return body
    chunks: list[str] = []
    appended: set[Path] = set()
    for ref in includes:
        path = resolve_skill_include(skill_path, ref)
        if path is None or path in appended:
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            continue
        appended.add(path)
        header = _REFERENCE_HEADER.format(repo_path=_repo_relative_path(path))
        chunks.append(f"{header}\n\n{text}")
    if not chunks:
        return body
    return "".join((body, "\n\n", "\n\n".join(chunks)))


def append_report_template(skill_path: Path, body: str) -> str:
    """Append a nonempty sibling report template after the skill instructions."""
    if not body:
        return ""
    template_path = _report_template_path(skill_path)
    if not template_path.is_file():
        return body
    template = template_path.read_text(encoding="utf-8").strip()
    if not template:
        return body
    header = _REPORT_TEMPLATE_HEADER.format(repo_path=_repo_relative_path(template_path))
    return "".join((body, "\n\n", header, "\n\n", template))
