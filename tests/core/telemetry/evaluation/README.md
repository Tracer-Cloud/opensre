# Telemetry investigation baseline (draft)

Measure the existing `core.telemetry.investigation.investigate` entrypoint before
changing its prompt, output format, or evidence access. Generation uses the real
redaction, input budget, no-tool synthesis and killable model process. It does
not run a watch, contact ClickHouse, or deliver notifications.

## Baseline status

- Twelve **synthetic, agent-authored** fixtures; all await SRE review. They are
  proposed grading expectations, not independently verified ground truth.
- No live investigator scores or human/judge agreement are established by this
  draft. Unit tests use injected investigators/judges and make no model calls.
- The report remains free text. The deterministic check verifies the retained
  detector-count prefix only; it does not validate every count in model prose,
  citation existence, or causal support. Those need structured reports in the
  next slice. Semantic scores are advisory, with no release threshold yet.

## Case design and review

`cases.json` keeps the investigator's `incident` separate from evaluator-only
`expectations`. No case ID, category, family, split or grading labels enter the
production investigator. Its redacted input payload is retained verbatim in the
run artifact. The scorer sees that payload and the grading expectations; it
never fetches additional telemetry or invokes the investigator.

The four groups cover clear failure mechanisms, insufficient evidence,
misleading samples/conflicting messages, and untrusted/noisy telemetry. The
authentication pair adds decisive evidence in the second case: an invalid token
alone does not prove expiry, but a correlated validator log can establish expiry
for the selected request. Sample evidence never proves prevalence across all
detected failures.

Development/holdout splits are grouped by incident family. Holdout cases are
repository-visible; keep them out of prompt tuning and record when they are
used. This tiny synthetic holdout is not a production accuracy estimate.

An SRE should review each case's observations, strongest justified conclusion,
unsupported conclusions and acceptable next checks before setting
`review_status` to `sre_reviewed`. Candidate causes may be mentioned as explicit
unknowns or next checks; do not grade by forbidden-word matching.

## Generate baseline reports (live, opt-in)

Run from the repository root with the normal OpenSRE environment installed and
an API model configured through the existing OpenSRE model setup. CLI model
backends are rejected by the production investigator. This command consumes
model quota, takes up to the production synthesis deadline per run, and never
runs automatically in pytest or ordinary CI.

```bash
uv run python -m tests.core.telemetry.evaluation.runner generate \
  --case auth_rejection --repeats 1 --output work/triage-smoke.jsonl

uv run python -m tests.core.telemetry.evaluation.runner generate \
  --split development --repeats 3 --output work/triage-baseline.jsonl

uv run python -m tests.core.telemetry.evaluation.runner summarize \
  work/triage-baseline.jsonl
```

Options: `--cases` defaults to the included dataset; `--case` optionally selects
an exact ID; `--split` defaults to `development` (`holdout`/`all` are explicit);
`--repeats` defaults to three and must be positive; `--output` is required and
must not exist. Select a compatible split when selecting a holdout case.

The JSONL manifest captures revision, dirty-worktree status, dataset/source
hashes, synthesis function source (including its prompt), provider, parent-side
resolved model ID, Python version and production-default generation policy.
Generation settings remain the production defaults; the current entrypoint does
not expose exact provider request parameters, returned model identity, token
usage or price. These are recorded as unavailable rather than estimated. The
child revalidates the provider, but the parent model ID is not proof of the
model actually served by a provider alias. Keep configuration fixed during a run.

Results flush after each attempt. Partial synthesis fallbacks, raised errors,
and interrupted/missing runs stay visible. Summary/scoring recover an incomplete
final JSONL write, flag it explicitly, and still reject damaged complete rows.
Generation exits nonzero if any
attempt is partial or failed. Existing files are never overwritten; artifacts
are created with owner-only file permissions where supported. Keep generated
artifacts under ignored `work/`; do not commit real telemetry or credentials.

## Score saved reports with DeepEval (live, opt-in)

DeepEval currently requires Rich below 15 while OpenSRE requires Rich 15 or
newer. The judge therefore has a **separate locked uv project**. It reads saved
JSONL artifacts and imports no OpenSRE runtime modules. The application lockfile
and runtime dependencies are unchanged.

Configure credentials for the chosen judge model through DeepEval's supported
provider setup. `--model` is required; keep the identifier fixed for comparisons.
No Confident AI login is needed. Judge model requests still send the synthetic
evidence/report/rubric to that model provider. The telemetry opt-out below is
separate from model traffic.

```bash
DEEPEVAL_TELEMETRY_OPT_OUT=1 uv run --locked \
  --project tests/core/telemetry/evaluation/judge \
  python tests/core/telemetry/evaluation/judge/score.py \
  --runs work/triage-baseline.jsonl \
  --output work/triage-baseline-scores.jsonl \
  --model gpt-4.1
```

The example model is a selector, not a recommendation or a measured judge.
Use the same command with a new output path to rescore without rerunning the
investigator. Output files must not exist.

Two custom G-Eval metrics return normalized scores and reasons:

1. **Evidence support:** claims follow from available evidence; causal claims,
   population-wide claims and recovery declarations require support.
2. **Diagnostic usefulness:** strongest justified finding, important unknowns,
   and a targeted next check. Generic abstention loses credit on decisive cases.

The scorer reads and hashes one input snapshot before judging. Its JSONL output
starts with its source hash (rubric identity), DeepEval version, judge model
selector, generation manifest and input snapshot hash. Each completed or failed
metric is flushed immediately, followed by a per-attempt result; the last line
is a summary. Interruptions preserve completed work without requiring an
overwrite. There is no automatic resume/merge command in this draft.

Each metric has its own failure status. If one fails, the other metric's score
and reason remain available. An unexpected judge failure also preserves metrics
already received. Per-case summaries include scored `n`, mean, minimum and
maximum; metric, generation,
judge and missing-run counts are separate. Failures are never assigned perfect
scores or hidden in an overall mean. Partial reports can receive semantic
scores while still counting as partial execution. Nonzero exit signals incomplete
generation/judging, not a semantic threshold failure.

Before trusting these scores, have an SRE rank a few good/bad reports per case
(including confident-but-unsupported and unnecessarily vague answers), then
compare judge rankings and inspect disagreements. G-Eval is nondeterministic;
keep raw reports, judge reasons and repeated runs. This draft supplies the
rubrics, not evidence that they agree with SRE judgment.

## Offline checks and iteration

```bash
uv run python -m pytest tests/core/telemetry/test_evaluation.py
```

Ordinary pytest checks dataset validity, family-split isolation, evaluator-label
separation, saved-payload redaction, duplicate/tampered run rejection, failure
accounting, snapshot hashing/truncated-tail recovery, interruption checkpoints,
and repeated-score aggregation. It imports no DeepEval dependency.
The new tests join the existing default test selection; no live CI workflow is
added. Do not put paid model calls in the default test suite.

Next: SRE review the cases, calibrate the judge, generate and retain baseline v0,
then compare structured-report v1 on identical evidence/models. Add the scoped
tool-calling investigator only after those comparisons. Improvements must be
shown per case alongside completion/abstention behavior; an agent that always
says "unknown" must not win by avoiding every diagnosis.

The stacked [calibration workflow](CALIBRATION.md) prepares neutral review
material, compares attributed human ratings with judge scores, and produces
failure-class scorecards from saved investigator runs. Its authored examples
are explicitly separate from live investigator results.
