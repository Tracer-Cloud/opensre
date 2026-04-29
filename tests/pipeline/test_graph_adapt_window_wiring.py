"""Graph-level smoke tests for the adapt_window node wiring.

The adapt_window node sits on the loop-back edge of the investigation
pipeline: ``diagnose → adapt_window → plan_actions``. Terminal paths
(``opensre_eval``, ``publish``) bypass it. These tests assert that
contract by inspecting the compiled LangGraph rather than running it.

Per-node behaviour is tested elsewhere (``tests/nodes/adapt_window/``),
and the routing function itself is tested in
``test_route_investigation_eval.py``. This file only verifies the
WIRING.

Implementation note: LangGraph's public ``get_graph().edges`` cannot
statically traverse past an ``add_conditional_edges`` call that uses
dynamic ``Send`` fan-out (``distribute_hypotheses``) — every edge after
that point is dropped from the introspection view. We therefore read
``builder.branches`` (conditional edges with their path maps) and
``builder.edges`` (unconditional edges) directly. Both are stable,
documented attributes of LangGraph's StateGraph.
"""

from __future__ import annotations

from app.pipeline.graph import build_graph


def _builder():  # noqa: ANN202 - internal LangGraph type, type-stub varies
    return build_graph().builder


def _branch_ends(source: str, branch_name: str) -> dict[str, str] | None:
    """Return the path-map dict for ``add_conditional_edges(source, fn)``
    where the function name is ``branch_name``, or None if not present."""
    branches = _builder().branches.get(source, {})
    branch = branches.get(branch_name)
    return getattr(branch, "ends", None) if branch is not None else None


def _unconditional_edges() -> set[tuple[str, str]]:
    return {(src, dst) for src, dst in _builder().edges}


def _nodes() -> set[str]:
    # ``builder.nodes`` includes only user-added nodes (no __start__/__end__).
    return set(_builder().nodes.keys())


def test_adapt_window_node_is_registered() -> None:
    assert "adapt_window" in _nodes()


def test_loop_path_goes_through_adapt_window() -> None:
    """When ``route_investigation_loop`` returns ``"investigate"``, the
    graph must enter ``adapt_window`` BEFORE re-entering ``plan_actions``.
    Without this edge the loop would skip adaptation entirely."""
    ends = _branch_ends("diagnose", "route_investigation_loop")
    assert ends is not None, "diagnose must have a conditional edge via route_investigation_loop"
    assert ends.get("investigate") == "adapt_window"


def test_adapt_window_unconditionally_returns_to_plan_actions() -> None:
    """``adapt_window`` is a pass-through: whether or not it widens the
    window, the next node is always ``plan_actions``."""
    assert ("adapt_window", "plan_actions") in _unconditional_edges()


def test_terminate_paths_bypass_adapt_window() -> None:
    """Terminal routing decisions go straight from ``diagnose`` to
    ``opensre_eval`` or ``publish`` — adaptation is never run on a
    terminating iteration."""
    ends = _branch_ends("diagnose", "route_investigation_loop")
    assert ends is not None
    assert ends.get("opensre_eval") == "opensre_eval"
    assert ends.get("publish") == "publish"
    # And nothing else feeds adapt_window:
    incoming_to_adapt = [(src, dst) for src, dst in _builder().edges if dst == "adapt_window"]
    incoming_branches_to_adapt = [
        (src, branch_fn, key)
        for src, branches in _builder().branches.items()
        for branch_fn, branch in branches.items()
        for key, target in (getattr(branch, "ends", None) or {}).items()
        if target == "adapt_window"
    ]
    assert incoming_to_adapt == []  # no unconditional edges feed adapt_window
    assert incoming_branches_to_adapt == [("diagnose", "route_investigation_loop", "investigate")]


def test_no_direct_diagnose_to_plan_actions_edge() -> None:
    """Regression guard: before this PR, ``diagnose → plan_actions`` was
    a direct conditional edge. After the wiring change it must route
    through ``adapt_window``. A direct edge here would mean the rule is
    being silently skipped."""
    ends = _branch_ends("diagnose", "route_investigation_loop")
    assert ends is not None
    assert ends.get("investigate") != "plan_actions"
    assert ("diagnose", "plan_actions") not in _unconditional_edges()


def test_pre_loop_path_unchanged() -> None:
    """Pre-loop edges (resolve_integrations → plan_actions → ... → diagnose)
    are untouched by this wiring change. The middle of that chain is the
    parallel-hypotheses fan-out introduced by an earlier PR — the
    adapt_window wiring change only affects the diagnose loop-back."""
    edges = _unconditional_edges()
    assert ("resolve_integrations", "plan_actions") in edges
    assert ("investigate_hypothesis", "merge_hypothesis_results") in edges
    assert ("merge_hypothesis_results", "diagnose") in edges
