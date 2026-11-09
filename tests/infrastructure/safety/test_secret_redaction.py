"""Structured redaction must happen before JSON escapes credential delimiters."""

import json

from infrastructure.safety.secret_redaction import redact_values


class Diagnostic:
    def __str__(self) -> str:
        return "password=hidden"


def test_redacts_nested_credentials_before_json_with_default_policy() -> None:
    secret = "A" * 40
    source = {"logs": ({"message": "aws_secret_access_key=\n" + secret}, Diagnostic())}
    result = redact_values(source)
    encoded = json.dumps(result)
    assert secret not in encoded
    assert "hidden" not in encoded
    assert json.loads(encoded) == {
        "logs": [
            {"message": "aws_secret_access_key=[REDACTED:aws_secret]"},
            "[REDACTED:password]",
        ]
    }
    assert source["logs"][0]["message"].endswith(secret)


def test_custom_policy_covers_keys_and_nested_values_without_mutation() -> None:
    source = {7: ("sensitive", {"sensitive": [None, True, 2, 1.5]})}

    def policy(text: str) -> str:
        return text.replace("sensitive", "[redacted]")

    assert redact_values(source, redactor=policy) == {
        "7": ["[redacted]", {"[redacted]": [None, True, 2, 1.5]}]
    }
    assert source == {7: ("sensitive", {"sensitive": [None, True, 2, 1.5]})}
