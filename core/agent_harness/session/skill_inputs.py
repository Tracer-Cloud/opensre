"""Declarative input contracts for recurring skills.

A schedule persists ``skill_inputs`` next to the pinned skill. Each recurring
skill declares here which keys it accepts, which are required, and how each key
appears as an ``opensre cron add`` option, so the CLI, the schedule-offer tool,
and the yes-expansion share one table instead of per-skill branches.

Leaf module (stdlib only): the schedule offer in this package renders from it
and ``prompts/`` depends on this package, so it must not import ``prompts``.
Callers pass canonical skill names (see ``prompts.skills.naming``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

__all__ = (
    "RECURRING_SKILL_INPUT_CONTRACTS",
    "SkillInput",
    "SkillInputContract",
    "skill_input_contract",
    "skill_input_flags",
    "validate_recurring_skill_inputs",
)


@dataclass(frozen=True, slots=True)
class SkillInput:
    """One persisted schedule input and its CLI spelling."""

    key: str
    flag: str
    required: bool = False
    positive_int: bool = False


@dataclass(frozen=True, slots=True)
class SkillInputContract:
    """Inputs one recurring skill accepts; ``exclusive`` pairs may not both be set."""

    inputs: tuple[SkillInput, ...] = ()
    exclusive: tuple[tuple[str, str], ...] = ()

    def input_for(self, key: str) -> SkillInput | None:
        return next((item for item in self.inputs if item.key == key), None)


_REPOSITORY_OWNER = SkillInput("owner", "--owner", required=True)
_REPOSITORY_NAME = SkillInput("repo", "--repo", required=True)

RECURRING_SKILL_INPUT_CONTRACTS: Mapping[str, SkillInputContract] = MappingProxyType(
    {
        "delivering-morning-briefings": SkillInputContract(
            inputs=(SkillInput("city", "--city"),),
        ),
        "reporting-github-ci-failures": SkillInputContract(
            inputs=(
                _REPOSITORY_OWNER,
                _REPOSITORY_NAME,
                SkillInput("branch", "--branch"),
                SkillInput("pr_number", "--pr", positive_int=True),
            ),
            exclusive=(("branch", "pr_number"),),
        ),
        "fixing-github-security-alerts": SkillInputContract(
            inputs=(
                _REPOSITORY_OWNER,
                _REPOSITORY_NAME,
                SkillInput("workspace", "--workspace"),
            ),
        ),
    }
)

_EMPTY_CONTRACT = SkillInputContract()


def skill_input_contract(skill_name: str) -> SkillInputContract:
    """Return the input contract for canonical ``skill_name`` (empty when it has none)."""
    return RECURRING_SKILL_INPUT_CONTRACTS.get(skill_name.strip(), _EMPTY_CONTRACT)


def skill_input_flags(skill_name: str, inputs: Mapping[str, str]) -> list[str]:
    """Render persisted ``inputs`` as ``cron add`` arguments in contract order."""
    args: list[str] = []
    for item in skill_input_contract(skill_name).inputs:
        value = str(inputs.get(item.key, "") or "").strip()
        if value:
            args.extend([item.flag, value])
    return args


def _skills_accepting(key: str) -> tuple[str, ...]:
    return tuple(
        name
        for name, contract in RECURRING_SKILL_INPUT_CONTRACTS.items()
        if contract.input_for(key) is not None
    )


def _label(key: str, contract: SkillInputContract, *, as_flags: bool) -> str:
    if not as_flags:
        return key
    item = contract.input_for(key)
    if item is not None:
        return item.flag
    for other in RECURRING_SKILL_INPUT_CONTRACTS.values():
        found = other.input_for(key)
        if found is not None:
            return found.flag
    return key


def _join(labels: list[str]) -> str:
    if len(labels) <= 2:
        return " and ".join(labels)
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def validate_recurring_skill_inputs(
    skill_name: str,
    supplied: Mapping[str, str],
    *,
    as_flags: bool = False,
) -> dict[str, str]:
    """Return the persisted inputs for ``skill_name`` from every option a caller collected.

    ``supplied`` may carry keys of any skill (a CLI passes all its options);
    blank values are ignored. Raises ``ValueError`` when a non-blank key does not
    belong to the skill, a required key is missing, an exclusive pair is both
    set, or a positive-integer key is not one. ``as_flags`` phrases the message
    with CLI options instead of input keys.
    """
    name = skill_name.strip()
    contract = skill_input_contract(name)
    present = {key: value.strip() for key, value in supplied.items() if value and value.strip()}

    foreign = [key for key in present if contract.input_for(key) is None]
    if foreign:
        key = foreign[0]
        owners = _skills_accepting(key)
        label = _label(key, contract, as_flags=as_flags)
        if as_flags:
            skills = " or ".join(owners) if owners else "a recurring skill"
            raise ValueError(f"{label} is only valid with --kind recurring_skill --skill {skills}.")
        skills = " or ".join(owners) if owners else "another recurring skill"
        raise ValueError(f"{label} is only valid for {skills}.")

    missing = [item.key for item in contract.inputs if item.required and item.key not in present]
    if missing:
        labels = _join([_label(key, contract, as_flags=as_flags) for key in missing])
        target = f"skill {name}" if as_flags else name
        verb = "is" if len(missing) == 1 else "are"
        raise ValueError(f"{labels} {verb} required for {target}.")

    for first, second in contract.exclusive:
        if first in present and second in present:
            a = _label(first, contract, as_flags=as_flags)
            b = _label(second, contract, as_flags=as_flags)
            raise ValueError(f"Use either {a} or {b}, not both.")

    for item in contract.inputs:
        if not item.positive_int or item.key not in present:
            continue
        try:
            positive = int(present[item.key]) > 0
        except ValueError:
            positive = False
        if not positive:
            raise ValueError(f"{item.key} must be a positive integer.")

    return {item.key: present[item.key] for item in contract.inputs if item.key in present}
