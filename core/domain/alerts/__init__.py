"""Alert-domain models, ingestion, routing, and planning rules.

- ``alert_source.py``  — resolve alert vendor, map to tool sources, relevance scoring
- ``fields.py``        — shared alert field precedence and payload-shape helpers
- ``extraction.py``    — deterministic field extraction for the extract_alert stage
- ``inbox.py``         — in-process alert queue

Alert intake HTTP is served by :mod:`gateway.web.webapp` ``POST /alerts`` (started from
the interactive shell when ``alert_listener_enabled`` is set in REPL config).
"""
