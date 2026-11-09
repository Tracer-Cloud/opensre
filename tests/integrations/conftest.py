"""No AWS test may reach a real account.

A test that misses a patch must fail with "no credentials", never make a live
STS / EC2 / S3 call against whatever ``~/.aws`` profile the developer has. This
happened once: an AWS role-path test in this tree bypassed its ``boto3.client``
fake and called ``sts:AssumeRole`` on a personal account. These env vars make
boto3's chain find nothing (no shared files, no instance metadata) unless a
test sets its own; scoped to ``tests/integrations`` because that is where the
AWS verifier, catalog, and vendor client tests live.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_real_aws_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
