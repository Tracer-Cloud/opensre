"""Launch banner status must not import the action-skill harness graph."""

from __future__ import annotations

import subprocess
import sys


def test_load_launch_status_does_not_import_skill_harness() -> None:
    """Skills chip is a filesystem count — not ``list_action_skills`` / prompt_toolkit."""
    probe = (
        "import sys; "
        "import integrations.github; "
        "print('EAGER_LEDGER', 'integrations.github.tools.ci_fix.ledger' in sys.modules); "
        "from surfaces.shared.terminal.banner.banner_state import load_launch_status; "
        "status = load_launch_status(); "
        "heavy = [n for n in sys.modules if n == 'prompt_toolkit' "
        "or n.startswith('core.agent_harness.prompts.skills.loader') "
        "or n.startswith('core.agent_harness.spi')]; "
        "print('STATUS', status.skill_count, status.ci_fix_count); "
        "print('HEAVY', ','.join(sorted(heavy)[:12]) or 'none')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "HEAVY none" in result.stdout, result.stdout + result.stderr
    assert "EAGER_LEDGER False" in result.stdout, result.stdout + result.stderr
    assert "STATUS" in result.stdout
