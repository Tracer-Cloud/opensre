"""Discover validated workflow cards and load their bodies and local includes."""

from __future__ import annotations

import logging
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from pydantic import ValidationError

from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.prompts.skills.contracts import ActionSkill, SkillCatalog, SkillToolCall
from core.agent_harness.prompts.skills.demo_menu import (
    demo_handoffs,
    demo_skills,
    populate_demo_menu,
)
from core.agent_harness.prompts.skills.naming import normalize_skill_name
from core.agent_harness.prompts.skills.validation import (
    SkillCard,
    SkillCardError,
    parse_frontmatter,
)

__all__ = (
    "ActionSkill",
    "SKILLS_HEADER",
    "SkillToolCall",
    "getting_started_skills",
    "list_action_skills",
    "load_skill_body",
    "load_skill_reference",
    "load_skills_block",
    "load_skills_index",
    "skill_reference_names",
    "skills_dir",
    "read_skill_catalog",
    "validate_skill_file",
)

SKILLS_HEADER = f"{'=' * 40} SKILLS INDEX {'=' * 40}"

_PACKAGE_SKILL_FILENAME = "SKILL.md"
_REPORT_TEMPLATE_SUFFIX = "_report.md"
_REFERENCES_DIRNAME = "references"
_REPO_SKILLS_PREFIX = "core/agent_harness/prompts/skills"
_REPORT_TEMPLATE_HEADER = "REPORT TEMPLATE from `{repo_path}` (fill exactly; keep all headings):"
_REFERENCE_HEADER = "SHARED RULES from `{repo_path}`:"
_REFERENCE_NAME_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


logger = logging.getLogger(__name__)


def skills_dir() -> Path:
    """Return the directory that holds the bundled skill markdown files."""
    return Path(__file__).parent


def _repo_relative_path(path: Path) -> str:
    """Return a stable repo-relative path for prompt references."""
    try:
        relative = path.relative_to(skills_dir())
    except ValueError:
        return path.name
    return f"{_REPO_SKILLS_PREFIX}/{relative.as_posix()}"


def _package_skill_path(package_dir: Path) -> Path | None:
    """Return the skill recipe path inside a package directory, if present."""
    for candidate in (
        package_dir / _PACKAGE_SKILL_FILENAME,
        package_dir / f"{package_dir.name}.md",
    ):
        if candidate.is_file():
            return candidate
    return None


def _iter_skill_paths(directory: Path) -> list[Path]:
    """Return skill recipe paths in stable order (packages, nested packages, flat files).

    A package directory may nest one level of child skill packages (e.g.
    ``onboarding-github-ci/a-analyzing-github-ci-performance/SKILL.md``); each child follows its
    parent so related skills stay adjacent in the index.
    """
    paths: list[Path] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill_file = _package_skill_path(child)
        if skill_file is not None:
            paths.append(skill_file)
        for nested in sorted(child.iterdir()):
            if not nested.is_dir() or nested.name.startswith("."):
                continue
            nested_file = _package_skill_path(nested)
            if nested_file is not None:
                paths.append(nested_file)
    paths.extend(
        path
        for path in sorted(directory.glob("*.md"))
        if path.name not in {"AGENTS.md", "README.md"}
        and not path.name.endswith(_REPORT_TEMPLATE_SUFFIX)
    )
    return paths


def _skill_references(skill_path: Path) -> tuple[str, ...]:
    """Return sorted stems of the skill's bundled ``references/*.md`` files."""
    if skill_path.name != _PACKAGE_SKILL_FILENAME:
        return ()
    references_dir = skill_path.parent / _REFERENCES_DIRNAME
    if not references_dir.is_dir():
        return ()
    return tuple(sorted(path.stem for path in references_dir.glob("*.md") if path.is_file()))


def _report_template_path(skill_path: Path) -> Path:
    """Return the sibling report template path for a skill recipe."""
    package_name = skill_path.parent.name
    if skill_path.name == _PACKAGE_SKILL_FILENAME:
        return skill_path.parent / f"{package_name}{_REPORT_TEMPLATE_SUFFIX}"
    return skill_path.with_name(f"{skill_path.stem}{_REPORT_TEMPLATE_SUFFIX}")


def _path_is_under(path: Path, root: Path) -> bool:
    """Return True when ``path`` is ``root`` or a file inside it."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_skill_include(skill_path: Path, ref: str) -> Path | None:
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
        if candidate.name == _PACKAGE_SKILL_FILENAME:
            continue
        if candidate.is_file() and candidate.suffix.lower() == ".md":
            return candidate
    return None


def _skill_body_with_includes(skill_path: Path, body: str, includes: tuple[str, ...]) -> str:
    if not body or not includes:
        return body
    chunks: list[str] = []
    appended: set[Path] = set()
    for ref in includes:
        path = _resolve_skill_include(skill_path, ref)
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


def _skill_body_with_optional_template(skill_path: Path, body: str) -> str:
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


def validate_skill_file(skill_path: Path) -> ActionSkill:
    """Validate one raw card, including the local files it includes."""
    raw = skill_path.read_text(encoding="utf-8")
    frontmatter, _body = parse_frontmatter(raw)
    card = SkillCard.model_validate(frontmatter)
    for ref in card.includes:
        if _resolve_skill_include(skill_path, ref) is None:
            raise SkillCardError(f"includes: cannot resolve in-tree Markdown file {ref!r}")
    return ActionSkill(
        name=card.name,
        description=card.description,
        path=skill_path,
        recurring=card.recurring,
        getting_started=card.getting_started,
        demo_order=card.demo_order,
        pre_execute=tuple(
            SkillToolCall(call.tool, MappingProxyType(call.args)) for call in card.pre_execute
        ),
        includes=tuple(card.includes),
    )


def read_skill_catalog() -> SkillCatalog:
    """Validate every discovered card; retain diagnostics for CI and runtime reporting."""
    directory = skills_dir()
    if not directory.is_dir():
        return SkillCatalog((), ())
    skills: list[ActionSkill] = []
    diagnostics: list[str] = []
    for path in _iter_skill_paths(directory):
        try:
            skills.append(validate_skill_file(path))
        except (OSError, UnicodeError, SkillCardError, ValidationError) as exc:
            diagnostics.append(f"{path}: {exc}")
    names = Counter(skill.name for skill in skills)
    labels = Counter(skill.getting_started for skill in skills if skill.getting_started)
    orders = Counter(skill.demo_order for skill in skills if skill.getting_started)
    valid: list[ActionSkill] = []
    for skill in skills:
        conflicts: list[str] = []
        if names[skill.name] > 1:
            conflicts.append(f"duplicate name {skill.name!r}")
        if skill.getting_started:
            if labels[skill.getting_started] > 1:
                conflicts.append("duplicate getting_started label")
            if orders[skill.demo_order] > 1:
                conflicts.append(f"duplicate demo_order {skill.demo_order}")
        if conflicts:
            diagnostics.append(f"{skill.path}: {', '.join(conflicts)}")
        else:
            valid.append(skill)
    populated = populate_demo_menu(tuple(valid))
    return SkillCatalog(populated.skills, (*diagnostics, *populated.diagnostics))


@lru_cache(maxsize=1)
def list_action_skills() -> tuple[ActionSkill, ...]:
    """Return valid skills; report and exclude broken cards without preventing startup."""
    catalog = read_skill_catalog()
    for diagnostic in catalog.diagnostics:
        logger.warning("Skipping invalid skill: %s", diagnostic)
    return catalog.skills


def getting_started_skills() -> tuple[ActionSkill, ...]:
    """Return the current selectable demos in menu order."""
    return demo_skills(list_action_skills())


def _index_line(skill: ActionSkill) -> str:
    recurring = " [recurring]" if skill.recurring else ""
    return f"- {skill.name} — {skill.description}{recurring}"


@lru_cache(maxsize=1)
def load_skills_index() -> str:
    """Return the compact SKILLS INDEX for the stable system prompt."""
    skills = list_action_skills()
    if not skills:
        return ""
    lines = [
        SKILLS_HEADER,
        "",
        "Compact catalog only — full skill bodies are NOT inlined here.",
        "Skill matches outrank a generic docs/how-to answer.",
        "Before answering, check this catalog for an action-shaped match",
        '(including "set up", "install", "onboard me", "demo", "audit", or "fix").',
        'For capability questions ("what can you do", "how can you help"),',
        "follow the getting-started instruction to answer first and offer /demo.",
        "When the user request matches a skill below, call skill_view(name) in",
        "this turn. Read its result before creating or revising its plan or",
        "calling its workflow tools. Request dependent update_plan calls in a",
        "later tool-call batch, after reading the skill's full instructions.",
        "",
    ]
    lines.extend(_index_line(skill) for skill in skills)
    return "".join(("\n".join(lines), "\n\n"))


def load_skills_block() -> str:
    """Return the skills section for the action envelope (the compact index).

    Historically this dumped every skill body. The harness is now thin: only
    the index is stable-cached; bodies load via ``skill_view``.
    """
    return load_skills_index()


def load_skill_body(name: str) -> str:
    """Return one skill's body with includes and report template, or ``\"\"`` if unknown."""
    needle = normalize_skill_name(name)
    if not needle:
        return ""
    for skill in list_action_skills():
        if skill.name == needle:
            try:
                current = validate_skill_file(skill.path)
                _frontmatter, body = parse_frontmatter(skill.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, SkillCardError, ValidationError) as exc:
                logger.warning("Skipping invalid skill %s: %s", skill.path, exc)
                return ""
            body = _skill_body_with_includes(skill.path, body, current.includes)
            body = _skill_body_with_optional_template(skill.path, body)
            if skill.name == ONBOARDING_SKILL_NAME:
                body += demo_handoffs(list_action_skills())
            return body
    return ""


def skill_reference_names(name: str) -> tuple[str, ...]:
    """Return the stems of a skill's on-demand ``references/*.md`` files.

    Distinct from ``ActionSkill.includes`` (frontmatter paths inlined into the
    body): these files stay out of the body and load via :func:`load_skill_reference`.
    """
    needle = normalize_skill_name(name)
    for skill in list_action_skills():
        if skill.name == needle:
            return _skill_references(skill.path)
    return ()


def load_skill_reference(name: str, reference: str) -> str:
    """Return one on-demand ``references/<reference>.md`` file of a skill, or ``""`` if unknown.

    ``reference`` must be a plain slug (no path separators), so a skill body can
    link only files inside its own ``references/`` directory.
    """
    needle = normalize_skill_name(name)
    slug = reference.strip().lower()
    if not needle or not _REFERENCE_NAME_RE.match(slug):
        return ""
    for skill in list_action_skills():
        if skill.name != needle:
            continue
        if skill.path.name != _PACKAGE_SKILL_FILENAME:
            return ""
        reference_path = skill.path.parent / _REFERENCES_DIRNAME / f"{slug}.md"
        if not reference_path.is_file():
            return ""
        try:
            return reference_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def clear_skills_caches() -> None:
    """Drop cached discovery/index (tests mutate on-disk skills)."""
    list_action_skills.cache_clear()
    load_skills_index.cache_clear()


# Back-compat for tests that call ``load_skills_block.cache_clear()``.
load_skills_block.cache_clear = clear_skills_caches  # type: ignore[attr-defined]
