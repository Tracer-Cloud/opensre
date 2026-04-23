"""LLM judge node — runs after diagnosis when ``opensre_evaluate`` is set."""

from __future__ import annotations

import logging
from typing import Any, cast

from langsmith import traceable

from app.output import debug_print, get_tracker
from app.state import InvestigationState

logger = logging.getLogger(__name__)


@traceable(name="node_opensre_llm_eval")
def node_opensre_llm_eval(state: InvestigationState) -> dict[str, Any]:
    """Score the investigation against ``opensre_eval_rubric`` (dataset scoring_points)."""
    tracker = get_tracker()
    tracker.start("opensre_llm_eval", "Evaluating vs OpenRCA rubric")

    rubric = (state.get("opensre_eval_rubric") or "").strip()
    if not rubric:
        debug_print("OpenSRE LLM eval skipped: no scoring_points on alert")
        tracker.complete("opensre_llm_eval", fields_updated=[])
        return {
            "opensre_llm_eval": {
                "skipped": True,
                "reason": "no scoring_points in alert payload",
            }
        }

    try:
        from app.integrations.opensre.llm_eval_judge import run_opensre_llm_judge

        result = run_opensre_llm_judge(state=cast(dict[str, Any], state), rubric=rubric)
    except Exception as exc:
        logger.exception("OpenSRE LLM eval failed")
        tracker.complete("opensre_llm_eval", fields_updated=[])
        return {"opensre_llm_eval": {"error": str(exc)}}

    tracker.complete("opensre_llm_eval", fields_updated=["opensre_llm_eval"])
    return {"opensre_llm_eval": result}
