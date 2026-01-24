"""Error handling for root cause diagnosis."""

from src.agent.output import debug_print


def check_evidence_sources(investigation: dict) -> tuple[bool, str | None]:
    """
    Check if any evidence sources were checked.

    Returns:
        (has_evidence, error_message)
    """
    evidence_sources_checked = investigation.get("evidence_sources_checked", [])
    if len(evidence_sources_checked) == 0:
        error_message = (
            "ERROR: No root cause has been identified because no information could be accessed. "
            "No evidence sources were successfully checked."
        )
        debug_print(error_message)
        return False, error_message
    return True, None
