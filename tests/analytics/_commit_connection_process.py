"""Emit commit fixtures through the real client to an isolated HTTP receiver."""

from __future__ import annotations

import json
import os
import sys

from config.account import AccountRecord, save_account_record, save_account_token
from infrastructure.analytics.capture import (
    capture_account_authenticated,
    capture_opensre_commit_created,
)
from infrastructure.analytics.provider import get_analytics, shutdown_analytics


def sign_in(account: str) -> None:
    """Persist the fixture credentials through the normal account store."""
    save_account_record(
        AccountRecord(
            user_id=f"user_integrity_{account}",
            organization_id="org_integrity",
            email=None,
            app_url=os.environ["OPENSRE_APP_URL"],
            signed_in_at="2026-09-21T00:00:00Z",
            token_expires_at="2099-01-01T00:00:00Z",
        )
    )
    save_account_token(f"osre_pat_integrity_{account}_not_a_real_token")


def main() -> None:
    scenario = sys.argv[1]
    if scenario in {"a", "b", "switch"}:
        sign_in("a" if scenario == "switch" else scenario)
    analytics = get_analytics()
    # These untrusted claims must never change the server-resolved identity.
    analytics.set_persistent_property("user_id", "user_spoofed")
    analytics.set_persistent_property("organization_id", "org_integrity")
    if scenario == "tagged":
        analytics.set_persistent_property("e2e_run_id", "commit_connection_fixture")
    capture_opensre_commit_created(
        workflow="github_ci_fix", commit_kind="repair", changed_file_count=2
    )
    if scenario == "switch":
        sign_in("b")
        capture_account_authenticated()
        capture_opensre_commit_created(
            workflow="github_ci_fix", commit_kind="repair", changed_file_count=1
        )
    shutdown_analytics(flush=True, timeout=10)
    print(json.dumps({"emitted": 2 if scenario == "switch" else 1}))


if __name__ == "__main__":
    main()
