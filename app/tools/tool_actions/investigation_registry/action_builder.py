"""Builder for creating InvestigationAction from functions."""

import inspect
from collections.abc import Callable
from typing import Any

from app.state import EvidenceSource
from app.tools.tool_actions.investigation_registry.docstring_parser import (
    extract_description,
    extract_inputs,
    extract_outputs,
    extract_use_cases,
)
from app.tools.tool_actions.investigation_registry.models import InvestigationAction


def build_action(
    name: str,
    func: Callable,
    source: EvidenceSource,
    requires: list[str] | None = None,
    availability_check: Callable[[dict[str, dict]], bool] | None = None,
    parameter_extractor: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
) -> InvestigationAction:
    """Build InvestigationAction from function and metadata."""
    docstring = inspect.getdoc(func) or ""
    description = extract_description(docstring)
    use_cases = extract_use_cases(docstring)
    inputs = extract_inputs(docstring, func)
    outputs = extract_outputs(docstring)

    if requires is None:
        requires = []
        sig = inspect.signature(func)
        for param_name, param in sig.parameters.items():
            if param.default == inspect.Parameter.empty:
                requires.append(param_name)

    return InvestigationAction(
        name=name,
        description=description,
        inputs=inputs,
        outputs=outputs,
        use_cases=use_cases,
        requires=requires,
        source=source,
        function=func,
        availability_check=availability_check,
        parameter_extractor=parameter_extractor,
    )
