"""Read the shipped loop templates: YAML frontmatter plus at most ten sentences each."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

MAX_TEMPLATE_SENTENCES = 10
_TEMPLATES_DIR = Path(__file__).parent
_SENTENCE_END = re.compile(r"(?<!\d)[.!?](?=\s|$)")


@dataclass(frozen=True, slots=True)
class LoopTemplate:
    """One shipped loop template."""

    template: str
    name: str
    description: str
    cron: str
    mode: str
    prompt: str


def loop_template_names() -> tuple[str, ...]:
    """Return the names of every shipped loop template, sorted."""
    return tuple(sorted(path.stem for path in _TEMPLATES_DIR.glob("*.md")))


def count_sentences(text: str) -> int:
    """Count one sentence per closing ``.``, ``!`` or ``?``; list numbers like ``1.`` do not count."""
    return len(_SENTENCE_END.findall(text))


@cache
def load_loop_template(template: str) -> LoopTemplate:
    """Read one shipped template; raise ``KeyError`` when no template has that name."""
    if template not in loop_template_names():
        raise KeyError(template)
    raw = (_TEMPLATES_DIR / f"{template}.md").read_text(encoding="utf-8")
    _, frontmatter, body = raw.split("---", 2)
    meta = yaml.safe_load(frontmatter) or {}
    return LoopTemplate(
        template=template,
        name=str(meta.get("name", "")).strip(),
        description=str(meta.get("description", "")).strip(),
        cron=str(meta.get("cron", "")).strip(),
        mode=str(meta.get("mode", "")).strip(),
        prompt=body.strip(),
    )


__all__ = [
    "MAX_TEMPLATE_SENTENCES",
    "LoopTemplate",
    "count_sentences",
    "load_loop_template",
    "loop_template_names",
]
