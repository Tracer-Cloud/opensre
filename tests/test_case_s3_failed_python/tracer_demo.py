#!/usr/bin/env python3
"""
Backward-compatible entrypoint for the S3 demo.
"""

from tests.test_case_s3_failed_python.test_orchestrator import main

if __name__ == "__main__":
    raise SystemExit(main())
