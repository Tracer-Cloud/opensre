"""Strict frontmatter validation shared by skill discovery and CI."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from config.constants.skills import ONBOARDING_SKILL_NAME, SKIP_DEMO_OPTION
from core.agent_harness.session.pending_choice import question_key

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _distinct_options(options: list[str]) -> list[str]:
    if len(set(options)) != len(options):
        raise ValueError("menu options must be distinct")
    return options


_Options = Annotated[
    list[_Text], Field(min_length=2, max_length=8), AfterValidator(_distinct_options)
]


class SkillCardError(ValueError):
    """A skill card cannot safely enter the catalog."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SkillMetadata(_StrictModel):
    """Required human-readable ownership, use cases, and prerequisites."""

    owner: _Text
    last_changed_by: _Text
    last_changed_at: date
    usecases: Annotated[list[_Text], Field(min_length=1)]
    requires: Annotated[list[_Text], Field(min_length=1)]
    version: _Text

    @field_validator("owner", "last_changed_by")
    @classmethod
    def person_name(cls, value: str) -> str:
        if "team" in value.lower():
            raise ValueError("must name a person")
        return value

    @field_validator("last_changed_at")
    @classmethod
    def change_date(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("must not be in the future")
        return value


class _EntryQuestion(_StrictModel):
    label: _Text
    title: _Text
    options: _Options
    multi_select: bool = False


class _EntryArguments(_StrictModel):
    title: _Text | None = None
    options: _Options | None = None
    note: str = ""
    questions: Annotated[list[_EntryQuestion], Field(min_length=2, max_length=6)] | None = None
    multi_select: bool = False
    allow_custom: bool = True

    @field_validator("questions")
    @classmethod
    def distinct_questions(cls, value: list[_EntryQuestion] | None) -> list[_EntryQuestion] | None:
        if value is not None and len({question_key(item.title) for item in value}) != len(value):
            raise ValueError("question titles must be distinct after whitespace and case folding")
        return value


class SkillEntryCall(_StrictModel):
    """A validated entry menu, checked again against the public tool schema on entry."""

    tool: Literal["ask_user_choice"]
    args: dict[str, Any]

    @field_validator("args")
    @classmethod
    def menu_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _EntryArguments.model_validate(value).model_dump(exclude_unset=True)


class SkillCard(_StrictModel):
    """The supported YAML fields of a main workflow card."""

    name: Annotated[_Text, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    description: _Text
    metadata: SkillMetadata
    recurring: bool = False
    getting_started: _Text | None = None
    demo_order: Annotated[int, Field(gt=0)] | None = None
    includes: list[_Text] = Field(default_factory=list)
    pre_execute: list[SkillEntryCall] = Field(default_factory=list, max_length=1)

    @model_validator(mode="after")
    def validate_demo(self) -> Self:
        if (self.getting_started is None) != (self.demo_order is None):
            raise ValueError("getting_started and demo_order must be declared together")
        if self.getting_started == SKIP_DEMO_OPTION:
            raise ValueError("getting_started cannot use the reserved Skip label")
        if self.name == ONBOARDING_SKILL_NAME:
            if len(self.pre_execute) != 1:
                raise ValueError("onboarding requires exactly one pre_execute menu")
            if "options" in self.pre_execute[0].args:
                raise ValueError("onboarding menu options are generated from demo metadata")
            if self.pre_execute[0].args.get("questions") or self.pre_execute[0].args.get(
                "multi_select"
            ):
                raise ValueError("onboarding requires one single-choice menu")
        for call in self.pre_execute:
            if call.args.get("questions"):
                continue
            if not call.args.get("title"):
                raise ValueError("pre_execute menu requires a title")
            if self.name != ONBOARDING_SKILL_NAME and not call.args.get("options"):
                raise ValueError("pre_execute menu requires options or questions")
        return self


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML mapping keys before they overwrite card fields."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError as exc:
                raise SkillCardError("YAML mapping keys must be scalar values") from exc
            if duplicate:
                raise SkillCardError(f"duplicate YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Read required YAML frontmatter and a nonempty Markdown body."""
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if not lines or lines[0] != "---":
        raise SkillCardError("card must begin with YAML frontmatter (---)")
    end = next((index for index in range(1, len(lines)) if lines[index] == "---"), None)
    if end is None:
        raise SkillCardError("frontmatter must end with ---")
    try:
        loaded = yaml.load("\n".join(lines[1:end]), Loader=_UniqueKeyLoader)
    except SkillCardError:
        raise
    except (yaml.YAMLError, ValueError) as exc:
        raise SkillCardError("malformed YAML frontmatter") from exc
    if not isinstance(loaded, dict) or any(not isinstance(key, str) for key in loaded):
        raise SkillCardError("frontmatter must be a mapping with string keys")
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise SkillCardError("skill body must not be empty")
    return loaded, body
