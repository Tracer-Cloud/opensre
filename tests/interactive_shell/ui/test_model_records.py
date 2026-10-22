"""Model connection records preserve full values at every terminal width."""

from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from surfaces.shared.terminal.tables.tables import render_models_table


@pytest.mark.parametrize("width", [40, 90, 160])
def test_model_connection_reflows_without_losing_literal_values(width: int) -> None:
    output = StringIO()
    console = Console(file=output, width=width, color_system=None)
    render_models_table(
        console,
        SimpleNamespace(
            provider="custom-openai",
            custom_openai_reasoning_model="reasoning-[bold]-東京",
            custom_openai_toolcall_model="toolcall-[red]-終わり",
        ),
        source="local [test]",
    )
    text = output.getvalue()
    assert "  LLM connection" in text
    assert "local [test]" in text
    assert "reasoning-[bold]-東京" in text
    assert "toolcall-[red]-終わり" in text
    assert "│" not in text
    if width == 40:
        assert "Reasoning model:" in text
        assert "Toolcall model:" in text
