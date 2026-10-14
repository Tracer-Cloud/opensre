"""JSON-safe restoration of CLI model responses."""

from __future__ import annotations

import json
import re

from infrastructure.safety.masking import MaskingRules

_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def _decode_tool_arguments(payload: object) -> bool:
    """Normalize encoded tool argument objects before restoring their strings."""
    if not isinstance(payload, dict):
        return False
    calls = payload.get("tool_calls")
    if not isinstance(calls, list):
        return False
    changed = False
    for call in calls:
        if not isinstance(call, dict):
            continue
        for key in ("input", "arguments"):
            value = call.get(key)
            if not isinstance(value, str):
                continue
            try:
                arguments = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(arguments, dict):
                call[key] = arguments
                changed = True
    return changed


def unmask_cli_response(text: str, masker: MaskingRules, *, tool_calls: bool = False) -> str:
    """Restore identifiers in prose and JSON without reinterpreting ordinary strings."""
    if not masker.placeholder_map:
        return text

    def restore_json_string(match: re.Match[str]) -> str:
        token = match.group(0)
        try:
            value: str = json.loads(token)
        except json.JSONDecodeError:
            return masker.unmask(token)
        restored = masker.unmask(value)
        return json.dumps(restored, ensure_ascii=False) if restored != value else token

    root_string = _JSON_STRING.search(text)
    if root_string is not None:
        prefix, suffix = text[: root_string.start()], text[root_string.end() :]
        if (
            not prefix.strip()
            and not suffix.strip()
            or prefix.strip() in {"```", "```json"}
            and suffix.strip() == "```"
        ):
            return prefix + restore_json_string(root_string) + suffix

    decoder = json.JSONDecoder()
    parts: list[str] = []
    cursor = 0
    # CLI responses may wrap JSON in markdown or precede it with prose.
    for match in re.finditer(r"[\[{]", text):
        start = match.start()
        if start < cursor:
            continue
        try:
            payload, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        parts.append(masker.unmask(text[cursor:start]))
        # Only tool-call envelopes declare these strings to be encoded JSON.
        json_text = (
            json.dumps(payload, ensure_ascii=False)
            if tool_calls and _decode_tool_arguments(payload)
            else text[start:end]
        )
        parts.append(_JSON_STRING.sub(restore_json_string, json_text))
        cursor = end
    parts.append(masker.unmask(text[cursor:]))
    return "".join(parts)
