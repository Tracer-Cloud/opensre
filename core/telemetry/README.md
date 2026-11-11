# Structured investigation reports

The foreground watch uses `structured_investigation.investigate`: a single,
tool-free synthesis with host-authored scope, measurements, evidence references,
failure reasons and schema version. `investigation.investigate` remains baseline
v0 for replay comparisons. Both use the same killable process helper; neither
has access to the interactive agent's tools.

`reports.Analysis` accepts only facts with evidence references, hypotheses with
supporting/contradicting references, unknowns, a next check and an analysis status.
All cited references must belong to the exact redacted input snapshot. References
are deterministic within that snapshot; they are not global span identities.
Text is redacted before retention. Oversized/invalid output is rejected rather
than shortened, preserving citation and credential boundaries.

`InvestigationReport` adds host metadata and the snapshot. The renderer preserves
the detector-count prefix and rejects reports above the delivery character budget.
`completed` and `inconclusive` describe analysis; host failures produce `partial`.
These statuses cannot open or close an incident. Empty trace/log results produce
inconclusive analysis without a model call. Missing telemetry never proves health.

The SQLite migration adds nullable `episodes.report_json`. The readable report,
structured report and opening event commit in one transaction. Legacy text stays
intact and has no structured artifact. Delivery retries never synthesize again.
The JSON CLI export exposes the saved structure and reference catalog.

Run `tests/core/telemetry/test_structured_reports.py` for citation isolation,
actual worker/deadline behavior, legacy upgrade, transaction rollback, source
failure and JSON export. The evaluation runner's `--candidate structured` uses
the same investigator against the baseline fixtures. No live semantic-quality
claim follows from these contract tests; SRE calibration and repeated live runs
remain necessary before changing release thresholds.
