"""Error handling for root cause diagnosis."""

from src.agent.nodes.publish_findings.render import console


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
        console.print(f"\n[bold red]❌ {error_message}[/]")
        console.print("\n[yellow]Possible reasons:[/]")
        console.print("  • API endpoints are unavailable")
        console.print("  • Authentication credentials are missing or invalid")
        console.print("  • Network connectivity issues")
        console.print("  • All evidence sources were skipped or failed")
        return False, error_message
    return True, None
