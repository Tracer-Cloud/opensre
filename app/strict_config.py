"""Shared strict Pydantic models for fail-fast configuration validation."""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class StrictConfigModel(BaseModel):
    """Base model that forbids unknown fields and suggests close matches."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_values(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        field_aliases = {
            name: field.alias
            for name, field in cls.model_fields.items()
            if field.alias and field.alias != name
        }
        allowed_fields = set(cls.model_fields) | set(field_aliases.values())
        extras = sorted(key for key in data if key not in allowed_fields)
        if not extras:
            return data

        details = []
        for field_name in extras:
            suggestion = get_close_matches(field_name, list(allowed_fields), n=1)
            if suggestion:
                details.append(f"'{field_name}' (did you mean '{suggestion[0]}'?)")
            else:
                details.append(f"'{field_name}'")

        if len(details) == 1:
            raise ValueError(f"Unexpected field {details[0]}.")
        raise ValueError(f"Unexpected fields {', '.join(details)}.")
