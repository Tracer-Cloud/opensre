"""Bundled skill entrypoints shared by startup and prompt assembly."""

from collections.abc import Mapping
from types import MappingProxyType

ONBOARDING_SKILL_NAME = "onboarding-github-ci"

# Children of the onboarding tree, in demo-menu order (A-D). Product code that
# branches on one of them reads it from here so a rename is a one-line change.
ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME = "analyzing-github-ci-performance"
SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME = "scheduling-github-ci-repairs"
DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME = "delegating-github-ci-repairs"
CONNECTING_SLACK_SKILL_NAME = "connecting-slack"

# Not a demo child: the analysis demo hands off to it when GitHub is not
# connected, cannot be read, or the user's repositories have no GitHub Actions.
ANALYZING_LOCAL_REPOSITORIES_SKILL_NAME = "analyzing-local-repositories"
REPORTING_GITHUB_CI_FAILURES_SKILL_NAME = "reporting-github-ci-failures"

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

# Live skills: signed catalog releases published to the OpenSRE app
# (``opensre skills push``) and pulled by every host in the background.
#: Exclusive local catalog for authoring; re-read whenever its files change.
SKILLS_DIR_ENV = "OPENSRE_SKILLS_DIR"
#: ``1``/``0`` to force background pulls on or off (default: on in release binaries).
SKILLS_AUTO_UPDATE_ENV = "OPENSRE_SKILLS_AUTO_UPDATE"
#: JSON ``{key_id: PEM}`` of extra trusted release keys; ignored by release binaries.
SKILLS_TRUSTED_KEYS_FILE_ENV = "OPENSRE_SKILLS_TRUSTED_KEYS_FILE"
#: Card/catalog contract version; a release declaring a newer one is not activated.
SKILLS_API_VERSION = 1
SKILLS_PULL_INTERVAL_SECONDS = 300
SKILLS_HTTP_TIMEOUT_SECONDS = 5.0
SKILLS_RELEASE_PATH = "/api/skills/release"
SKILLS_ROLLBACK_PATH = "/api/skills/rollback"
SKILLS_RELEASES_PATH = "/api/skills/releases"
#: GitHub OIDC audience the git-sync workflow requests for ``POST`` release.
SKILLS_SYNC_AUDIENCE = "https://app.opensre.com/skills/sync"
SKILLS_RELEASE_MAX_BYTES = 2 * 1024 * 1024
SKILLS_RELEASE_MAX_FILES = 500
#: Releases kept on disk; older ones are pruned after each fetch.
SKILLS_RELEASES_KEPT = 3
#: Release signing keys trusted by this binary (``key_id`` -> PEM public key).
#: Two slots let a new key be trusted before the server starts using it.
SKILLS_RELEASE_PUBLIC_KEYS: Mapping[str, str] = MappingProxyType(
    {
        # AWS KMS alias/opensre-skills-release-signing (ECC_NIST_P256), used by
        # app.opensre.com as SKILLS_SIGNING_KEY_ID=prod-1.
        "prod-1": (
            "-----BEGIN PUBLIC KEY-----\n"
            "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEqIjGLpdCv+iJzvtuyb7uTF/Xc1oj\n"
            "c7be3W+HEHNCjUYP2/oCOpthVWCrytTQCXlFegIclquxYUMO8pTg2HChDw==\n"
            "-----END PUBLIC KEY-----\n"
        ),
    }
)
