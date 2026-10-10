"""Shared source configuration form; query secrets are collected separately."""

from __future__ import annotations

from prompt_toolkit.application import Application

from integrations.signoz import validate_urls
from surfaces.shared.terminal.components.input_form import (
    InputField,
    InputFormError,
    build_input_form,
)


def build_connect_form(values: dict[str, str]) -> Application[list[str] | None]:
    """Keep incomplete edits local until explicit source submission."""

    def fields(current: dict[str, str]) -> list[InputField]:
        definitions = (
            ("name", "Source name", "A recognizable name for this source."),
            (
                "query_url",
                "SigNoz query URL",
                "Your existing SigNoz instance; no admin credential needed.",
            ),
            (
                "services",
                "Allowed services",
                "Comma-separated, exact service names. Required explicit scope.",
            ),
            (
                "ingress_url",
                "Gateway HTTPS ingress",
                "Your endpoint reachable by SigNoz; OpenSRE will not create a tunnel.",
            ),
        )
        return [
            InputField(
                name=name,
                label=label,
                description=description,
                status="Required",
                required=True,
                summary=current.get(name, ""),
            )
            for name, label, description in definitions
        ]

    def validate(current: dict[str, str]) -> list[str]:
        for name in ("name", "query_url", "services", "ingress_url"):
            if not current.get(name, "").strip():
                raise InputFormError("This field is required", field=name)
        try:
            validate_urls(current["query_url"], current["ingress_url"])
        except ValueError as exc:
            raise InputFormError(str(exc)) from exc
        result: list[str] = []
        for name, value in current.items():
            result.extend(["--" + name.replace("_", "-"), value])
        return result

    return build_input_form(
        values,
        title="/triage connect",
        heading="Connect SigNoz alerts",
        submit_label="Verify and connect",
        fields=fields,
        validate=validate,
    )
