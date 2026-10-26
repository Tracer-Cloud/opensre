"""The catalog facade must stay importable without vendor SDKs.

``configured_integration_health`` (launch banner) and harness adapter
registration import this module. Pulling kubernetes/boto3 at import time
made ``opensre --help`` and the welcome chips slower than peer CLIs.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_catalog_does_not_load_vendor_sdks() -> None:
    probe = (
        "import sys; import integrations.catalog; "
        "heavy = [n for n in sys.modules if n.split('.')[0] in "
        "{'kubernetes', 'boto3', 'botocore'}]; "
        "impl = 'integrations._catalog_impl' in sys.modules; "
        "print('HEAVY', ','.join(sorted(heavy)[:8]) or 'none'); "
        "print('IMPL', impl)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "HEAVY none" in result.stdout, result.stdout + result.stderr
    assert "IMPL False" in result.stdout, result.stdout + result.stderr
