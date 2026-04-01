"""Utilities for extracting metadata from function docstrings."""

import inspect
import re
from collections.abc import Callable


def extract_use_cases(docstring: str) -> list[str]:
    """Extract use cases from 'Useful for:' section in docstring."""
    if not docstring:
        return []
    useful_match = re.search(
        r"Useful for:\s*(.*?)(?:\n\n|\n[A-Z]|$)", docstring, re.DOTALL | re.IGNORECASE
    )
    if not useful_match:
        return []
    useful_text = useful_match.group(1).strip()
    return [line.strip().lstrip("- ") for line in useful_text.split("\n") if line.strip()]


def extract_inputs(docstring: str, func: Callable) -> dict[str, str]:
    """Extract input descriptions from 'Args:' section and function signature."""
    inputs: dict[str, str] = {}
    if not docstring:
        return inputs

    args_match = re.search(r"Args:\s*(.*?)(?:\n\n|\n[A-Z]|$)", docstring, re.DOTALL | re.IGNORECASE)
    if args_match:
        args_text = args_match.group(1).strip()
        for line in args_text.split("\n"):
            line = line.strip()
            if ":" in line:
                param, desc = line.split(":", 1)
                param = param.strip()
                desc = desc.strip()
                if param and desc:
                    inputs[param] = desc

    sig = inspect.signature(func)
    for param_name in sig.parameters:
        if param_name not in inputs:
            param = sig.parameters[param_name]
            if param.annotation != inspect.Parameter.empty:
                inputs[param_name] = f"Type: {param.annotation}"
            else:
                inputs[param_name] = "No description available"

    return inputs


def extract_outputs(docstring: str) -> dict[str, str]:
    """Extract output descriptions from 'Returns:' section."""
    outputs: dict[str, str] = {}
    if not docstring:
        return outputs

    returns_match = re.search(
        r"Returns:\s*(.*?)(?:\n\n|\n[A-Z]|$)", docstring, re.DOTALL | re.IGNORECASE
    )
    if returns_match:
        returns_text = returns_match.group(1).strip()
        if "Dictionary with" in returns_text:
            desc = returns_text.replace("Dictionary with", "").strip()
            outputs["result"] = desc
        else:
            outputs["result"] = returns_text

    return outputs


def extract_description(docstring: str) -> str:
    """Extract the main description (first line or paragraph)."""
    if not docstring:
        return ""
    lines = docstring.strip().split("\n")
    first_line = lines[0].strip()
    if first_line and not first_line.startswith("Useful for") and not first_line.startswith("Args"):
        return first_line
    return ""
