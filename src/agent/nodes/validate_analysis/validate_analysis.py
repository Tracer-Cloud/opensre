"""Validate root cause analysis for hallucinations and unrealistic values."""

from src.agent.nodes.rca_report_publishing.render import (
    console,
    render_step_header,
)
from src.agent.nodes.validate_analysis.claim_validation import validate_claim_against_evidence
from src.agent.nodes.validate_analysis.recommendations import generate_investigation_recommendations
from src.agent.state import InvestigationState


def main(state: InvestigationState) -> dict:
    """
    Main entry point for validating root cause analysis.

    Flow:
    1) Validate each claim separately against evidence
    2) Calculate validity score (validated vs total claims)
    3) Generate investigation recommendations if confidence is low
    4) Return validated/non-validated claims with evidence support
    """
    render_step_header(1, "Validate analysis")

    validated_claims = state.get("validated_claims", [])
    non_validated_claims = state.get("non_validated_claims", [])
    evidence = state.get("evidence", {})
    investigation = state.get("investigation", {})
    confidence = state.get("confidence", 0.0)

    if not validated_claims and not non_validated_claims:
        console.print("  [yellow]No claims to validate[/]")
        # Still return state updates to clear recommendations
        return {
            "investigation_recommendations": [],
            "validity_score": 0.0,
        }

    # Validate each claim
    validated_results = []
    non_validated_results = []

    for claim_data in validated_claims:
        claim = claim_data.get("claim", "")
        if claim:
            is_valid, issues = validate_claim_against_evidence(claim, evidence, investigation)
            if is_valid:
                validated_results.append({
                    **claim_data,
                    "validation_status": "validated",
                    "validation_issues": [],
                })
            else:
                # Move to non-validated if validation fails
                non_validated_results.append({
                    **claim_data,
                    "validation_status": "failed_validation",
                    "validation_issues": issues,
                })

    for claim_data in non_validated_claims:
        claim = claim_data.get("claim", "")
        if claim:
            is_valid, issues = validate_claim_against_evidence(claim, evidence, investigation)
            if is_valid:
                # Move to validated if it actually validates
                validated_results.append({
                    **claim_data,
                    "validation_status": "validated",
                    "validation_issues": [],
                })
            else:
                non_validated_results.append({
                    **claim_data,
                    "validation_status": "not_validated",
                    "validation_issues": issues,
                })

    # Calculate validity score
    total_claims = len(validated_results) + len(non_validated_results)
    validity_score = len(validated_results) / total_claims if total_claims > 0 else 0.0

    # Display results with detailed rendering
    from src.agent.nodes.rca_report_publishing.render import render_validated_claims

    render_validated_claims(validated_results, non_validated_results, validity_score, confidence)

    # Update confidence based on validity score (weighted average)
    # Final confidence = (original_confidence * 0.4) + (validity_score * 0.6)
    final_confidence = (confidence * 0.4) + (validity_score * 0.6)

    # Generate recommendations if confidence or validity is too low
    # Always clear recommendations first - they will be set if thresholds are not met
    investigation_recommendations = []
    confidence_threshold = 0.6  # 60% confidence threshold
    validity_threshold = 0.5  # 50% validity threshold

    # Get current loop count (don't increment yet - only increment if we actually loop)
    loop_count = state.get("investigation_loop_count", 0)

    if final_confidence < confidence_threshold or validity_score < validity_threshold:
        console.print(f"\n  [yellow]⚠️  Low confidence ({final_confidence:.0%}) or validity ({validity_score:.0%})[/]")
        investigation_recommendations = generate_investigation_recommendations(
            validated_results, non_validated_results, evidence, investigation
        )
        if investigation_recommendations:
            console.print(f"  [dim]Generated {len(investigation_recommendations)} investigation recommendations[/]")
            loop_count += 1
            console.print(f"  [cyan]→ Returning to hypothesis generation (loop {loop_count}/5)[/]")
    else:
        # Thresholds are met - explicitly clear recommendations to prevent looping
        console.print(f"\n  [green]✓ Confidence ({final_confidence:.0%}) and validity ({validity_score:.0%}) meet thresholds[/]")
        console.print("  [dim]Clearing investigation recommendations - proceeding to publish findings[/]")
        # Explicitly ensure recommendations are empty
        investigation_recommendations = []

    console.print(f"  [dim]Returning state: confidence={final_confidence:.0%}, validity={validity_score:.0%}, recommendations={len(investigation_recommendations)}, loop_count={loop_count}[/]")

    return {
        "validated_claims": validated_results,
        "non_validated_claims": non_validated_results,
        "validity_score": validity_score,
        "confidence": final_confidence,  # Update confidence based on validity
        "investigation_recommendations": investigation_recommendations,  # Explicitly set to empty list if thresholds met
        "investigation_loop_count": loop_count,
    }


def node_validate_analysis(state: InvestigationState) -> dict:
    """LangGraph node wrapper."""
    try:
        result = main(state)
        # Log that we're about to return - this helps verify the node completes
        console.print("  [dim]validate_analysis node completed, returning state update[/]")
        return result
    except Exception as e:
        # Log any exceptions
        import sys
        import traceback
        console.print(f"  [red]ERROR in validate_analysis: {e}[/]")
        print(f"ERROR in validate_analysis: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        # Return minimal state to allow graph to continue
        return {
            "investigation_recommendations": [],
            "validity_score": 0.0,
        }
