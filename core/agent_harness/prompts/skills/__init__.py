"""Discover validated skills and load their instructions on demand."""

from config.constants.skills import SKILLS_HEADER
from core.agent_harness.prompts.skills.catalog.contracts import (
    ActionSkill,
    SkillCatalog,
    SkillToolCall,
)
from core.agent_harness.prompts.skills.catalog.naming import (
    is_legacy_skill_name,
    normalize_skill_name,
)
from core.agent_harness.prompts.skills.catalog.registry import (
    find_action_skill,
    getting_started_skills,
    list_action_skills,
    read_skill_catalog,
)
from core.agent_harness.prompts.skills.catalog.schema import parse_frontmatter
from core.agent_harness.prompts.skills.content.body import load_skill_body
from core.agent_harness.prompts.skills.content.cache import clear_skills_caches
from core.agent_harness.prompts.skills.content.files import skills_dir
from core.agent_harness.prompts.skills.content.index import load_skills_index
from core.agent_harness.prompts.skills.content.references import (
    load_skill_reference,
    skill_reference_names,
)

__all__ = [
    "ActionSkill",
    "SKILLS_HEADER",
    "SkillCatalog",
    "SkillToolCall",
    "clear_skills_caches",
    "find_action_skill",
    "getting_started_skills",
    "is_legacy_skill_name",
    "list_action_skills",
    "load_skill_body",
    "load_skill_reference",
    "load_skills_index",
    "normalize_skill_name",
    "parse_frontmatter",
    "read_skill_catalog",
    "skill_reference_names",
    "skills_dir",
]
