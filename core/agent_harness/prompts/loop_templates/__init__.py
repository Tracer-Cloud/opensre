"""Loop templates: the short instructions a scheduled loop runs on every tick.

Each ``<name>.md`` here holds frontmatter (``name``, ``description``, ``cron``,
``mode``) and at most ten sentences. A loop created with ``cron add --template``
stores its name, so every tick runs the text of the installed release.
"""

from __future__ import annotations

from core.agent_harness.prompts.loop_templates.catalog import (
    MAX_TEMPLATE_SENTENCES,
    LoopTemplate,
    count_sentences,
    load_loop_template,
    loop_template_names,
)

__all__ = [
    "MAX_TEMPLATE_SENTENCES",
    "LoopTemplate",
    "count_sentences",
    "load_loop_template",
    "loop_template_names",
]
