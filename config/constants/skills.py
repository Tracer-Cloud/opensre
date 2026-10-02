"""Bundled skill entrypoints shared by startup and prompt assembly."""

ONBOARDING_SKILL_NAME = "onboarding-github-ci"

# Children of the onboarding tree, in demo-menu order (A-D). Product code that
# branches on one of them reads it from here so a rename is a one-line change.
ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME = "analyzing-github-ci-performance"
SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME = "scheduling-github-ci-repairs"
DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME = "delegating-github-ci-repairs"
CONNECTING_SLACK_SKILL_NAME = "connecting-slack"

# Master onboarding menu the host opens on skill entry. When the four onboarding
# children are present, the rows are the outcome choices below rather than each
# child's ``getting_started`` label. The automation row opens a follow-up; the
# shell row never reaches the model.
ONBOARDING_MENU_TITLE = "What would you like to do?"

# Master onboarding menu row the shell handles itself: no model turn, plain prompt.
SKIP_DEMO_OPTION = "Open the shell"

# First screen. Analyze is a leaf; the automation row opens the follow-up.
ANALYZE_REPO_OPTION = "Analyze & improve a repo (recommended)"
AUTOMATION_GROUP_OPTION = "Keep my CI/CD healthy automatically"
OUTCOME_MENU_OPTIONS = (
    ANALYZE_REPO_OPTION,
    AUTOMATION_GROUP_OPTION,
    SKIP_DEMO_OPTION,
)

# Follow-up for the automation row. The title repeats the row the user chose.
AUTOMATION_MENU_TITLE = AUTOMATION_GROUP_OPTION
LOCAL_REPAIR_OPTION = "Run continuously on this machine (recommended)"
CLOUD_REPAIR_OPTION = "Run one repair in OpenSRE Cloud"
SLACK_OPTION = "Connect Slack"
AUTOMATION_MENU_OPTIONS = (
    LOCAL_REPAIR_OPTION,
    CLOUD_REPAIR_OPTION,
    SLACK_OPTION,
)

# Asked after a local or cloud repair leaf, before any scan or gateway probe.
DEMO_REPO_PERMISSION_TITLE = "Create a private demo repository?"
DEMO_REPO_DECLINE_OPTION = "Don't create a demo repository"
REPAIR_MENU_OPTIONS = (
    LOCAL_REPAIR_OPTION,
    CLOUD_REPAIR_OPTION,
)

# Leaf label the model receives, in handoff order, keyed by skill name.
ONBOARDING_LEAF_CHOICES = (
    (ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME, ANALYZE_REPO_OPTION),
    (SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME, LOCAL_REPAIR_OPTION),
    (DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME, CLOUD_REPAIR_OPTION),
    (CONNECTING_SLACK_SKILL_NAME, SLACK_OPTION),
)

# Shared Markdown layout and compact prompt heading.
SKILL_FILENAME = "SKILL.md"
SKILL_REPORT_SUFFIX = "_report.md"
SKILLS_HEADER = f"{'=' * 40} SKILLS INDEX {'=' * 40}"
