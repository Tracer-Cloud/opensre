"""
Golden output test for the demo runner.

Runs tests/run_demo.py and compares stdout to the expected golden output.
Non-deterministic content (UUIDs, timestamps, durations) is normalized before comparison.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest


# Paths
PROJECT_ROOT = Path(__file__).parent.parent
DEMO_SCRIPT = Path(__file__).parent / "run_demo.py"
GOLDEN_FILE = Path(__file__).parent / "fixtures" / "expected_demo_output.txt"


def normalize_output(output: str) -> str:
    """
    Normalize non-deterministic content in demo output for stable comparison.
    
    Replaces:
    - UUIDs with <UUID>
    - Investigation URLs with stable placeholder
    - Timestamps like "02:13 UTC" with <TIMESTAMP>
    - Durations like "43.6 minutes" with <DURATION>
    - Costs like "$12.58" with <COST>
    - Memory values like "710.7 GB" with <MEMORY>
    """
    normalized = output
    
    # UUID pattern (32 hex chars with dashes)
    normalized = re.sub(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        '<UUID>',
        normalized,
        flags=re.IGNORECASE
    )
    
    # Investigation URLs - normalize the UUID part
    normalized = re.sub(
        r'(https://[^/]+/[^/]+/investigations/)<UUID>',
        r'\1<UUID>',
        normalized
    )
    
    # Timestamps like "02:13 UTC" or "00:13 UTC"
    normalized = re.sub(
        r'\d{2}:\d{2} UTC',
        '<TIMESTAMP>',
        normalized
    )
    
    # Durations like "43.6 minutes" or "2h 0m"
    normalized = re.sub(
        r'\d+\.?\d*\s*minutes?',
        '<DURATION>',
        normalized
    )
    normalized = re.sub(
        r'\d+h\s*\d+m',
        '<DURATION>',
        normalized
    )
    
    # Costs like "$12.58"
    normalized = re.sub(
        r'\$\d+\.\d{2}',
        '<COST>',
        normalized
    )
    
    # Memory values like "710.7 GB" or "700GB"
    normalized = re.sub(
        r'\d+\.?\d*\s*GB',
        '<MEMORY>',
        normalized
    )
    
    return normalized


@pytest.mark.integration
def test_demo_output_matches_golden():
    """
    Run the demo and verify output matches the golden file.
    
    This test ensures that make demo produces consistent output.
    """
    # Skip if golden file doesn't exist yet
    if not GOLDEN_FILE.exists():
        pytest.skip(
            f"Golden file not found at {GOLDEN_FILE}. "
            "Run 'make demo > examples/fixtures/expected_demo_output.txt' to create it."
        )
    
    # Run the demo script
    result = subprocess.run(
        [sys.executable, str(DEMO_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=120,  # 2 minute timeout for LLM calls
    )
    
    # Check it ran successfully
    assert result.returncode == 0, f"Demo failed with stderr:\n{result.stderr}"
    
    # Normalize both outputs
    actual = normalize_output(result.stdout)
    expected = normalize_output(GOLDEN_FILE.read_text())
    
    # Compare
    assert actual == expected, (
        f"Demo output does not match golden file.\n\n"
        f"=== EXPECTED (normalized) ===\n{expected}\n\n"
        f"=== ACTUAL (normalized) ===\n{actual}"
    )

