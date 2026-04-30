"""Tests for app/masking/secrets.py"""

import pytest

from app.masking.secrets import _REDACTED, RedactionResult, redact_secrets

# ---------------------------------------------------------------------------
# Fixtures — official AWS example values, never real credentials
# ---------------------------------------------------------------------------

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
FAKE_PEM_HEADER = "-----BEGIN RSA PRIVATE KEY-----"


# ---------------------------------------------------------------------------
# AWS Access Key
# ---------------------------------------------------------------------------


def test_aws_key_detected():
    result = redact_secrets(f"Dumped env: AWS_ACCESS_KEY_ID={FAKE_AWS_KEY}")
    assert FAKE_AWS_KEY not in result.text
    assert _REDACTED in result.text
    assert "aws_access_key_id" in result.findings


def test_aws_key_inside_longer_token_not_matched():
    # Word boundary \b prevents matching keys embedded in longer strings
    result = redact_secrets(f"token=X{FAKE_AWS_KEY}EXTRA")
    assert "aws_access_key_id" not in result.findings


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def test_jwt_detected():
    result = redact_secrets(f"Authorization: Bearer {FAKE_JWT}")
    assert FAKE_JWT not in result.text
    assert "jwt_token" in result.findings


def test_partial_jwt_header_only_not_matched():
    result = redact_secrets("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
    assert "jwt_token" not in result.findings


# ---------------------------------------------------------------------------
# PEM
# ---------------------------------------------------------------------------


def test_pem_rsa_header_detected():
    result = redact_secrets(f"{FAKE_PEM_HEADER}\nMIIEpAIBAAKCAQEA...")
    assert FAKE_PEM_HEADER not in result.text
    assert "pem_private_key" in result.findings


def test_pem_ec_header_detected():
    result = redact_secrets("-----BEGIN EC PRIVATE KEY-----\ndata")
    assert "pem_private_key" in result.findings


def test_pem_bare_header_detected():
    result = redact_secrets("-----BEGIN PRIVATE KEY-----\ndata")
    assert "pem_private_key" in result.findings


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_string_returns_cleanly():
    result = redact_secrets("")
    assert result.text == ""
    assert result.findings == []


def test_clean_log_text_unchanged():
    text = "Pod restarted due to OOMKilled. Memory limit: 512Mi. Node: worker-3."
    result = redact_secrets(text)
    assert result.text == text
    assert not result.has_findings


def test_idempotent():
    text = f"key: {FAKE_AWS_KEY}"
    first = redact_secrets(text)
    second = redact_secrets(first.text)
    assert first.text == second.text


def test_multiple_secrets_in_one_report():
    text = f"Key: {FAKE_AWS_KEY}\nAuth: Bearer {FAKE_JWT}"
    result = redact_secrets(text)
    assert result.has_findings
    assert FAKE_AWS_KEY not in result.text
    assert FAKE_JWT not in result.text


def test_findings_contain_pattern_names_not_values():
    result = redact_secrets(f"key={FAKE_AWS_KEY}")
    for finding in result.findings:
        assert FAKE_AWS_KEY not in finding
