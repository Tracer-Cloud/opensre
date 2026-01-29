"""Verify Slack API call behavior."""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

# Load .env
env_path = project_root / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

from app.agent.utils.slack_delivery import send_slack_report


def main():
    """Verify Slack API call behavior."""
    print("=== Slack API Call Verification ===\n")
    
    tracer_url = os.getenv("TRACER_API_URL")
    print(f"TRACER_API_URL: {tracer_url if tracer_url else 'NOT SET'}\n")
    
    print("--- Calling send_slack_report() ---")
    print("(Check stderr output for detailed logs)\n")
    
    # Capture stderr to see the logs
    send_slack_report("Test verification message")
    
    if not tracer_url:
        print("\n⚠️  Slack API will NOT be called because TRACER_API_URL is not set")
        print("   Set TRACER_API_URL in .env to enable Slack delivery")
    else:
        print(f"\n✓ Slack API will be called to: {tracer_url}/api/slack")
        print("  Check stderr output above for actual HTTP request/response")


if __name__ == "__main__":
    main()
