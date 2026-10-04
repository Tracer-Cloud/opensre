"""CI repair counter storage and display constants."""

CI_FIX_LEDGER_PATH_ENV = "OPENSRE_CI_FIX_LEDGER_PATH"
CI_FIX_LEDGER_LOCK_TIMEOUT_SECONDS = 2.0
CI_FIX_COUNT_LABEL = "CI/CD fixes"
#: Runs at one PR head that may finish a merge without settling a conflict before
#: the conflict goes to a person instead of another coding-agent attempt.
CI_FIX_UNSETTLED_MERGE_ATTEMPTS = 2

__all__ = [
    "CI_FIX_COUNT_LABEL",
    "CI_FIX_LEDGER_LOCK_TIMEOUT_SECONDS",
    "CI_FIX_LEDGER_PATH_ENV",
    "CI_FIX_UNSETTLED_MERGE_ATTEMPTS",
]
