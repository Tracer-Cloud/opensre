"""Bundled skill entrypoints shared by startup and prompt assembly."""

ONBOARDING_SKILL_NAME = "onboarding-github-ci"

# Children of the onboarding tree, in demo-menu order (A-C). Product code that
# branches on one of them reads it from here so a rename is a one-line change.
ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME = "analyzing-github-ci-performance"
SCHEDULING_GITHUB_CI_FIXES_SKILL_NAME = "scheduling-github-ci-fixes"
CONNECTING_SLACK_SKILL_NAME = "connecting-slack"

# Master onboarding menu row the shell handles itself: no model turn, plain prompt.
SKIP_DEMO_OPTION = "Skip the demo and open the shell"

# Shared Markdown layout and compact prompt heading.
SKILL_FILENAME = "SKILL.md"
SKILL_REPORT_SUFFIX = "_report.md"
SKILLS_HEADER = f"{'=' * 40} SKILLS INDEX {'=' * 40}"
