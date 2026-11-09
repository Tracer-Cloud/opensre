# Calibrating the investigator baseline (stacked draft)

This builds on the baseline tooling in #6994. It adds review material and offline
comparison/reporting; it does not establish human judgments or live model scores.
All source cases and authored reports still await SRE review.

## Prepare a review pack

From the repository root, using the normal OpenSRE environment:

```bash
uv run python -m tests.core.telemetry.evaluation.calibration prepare \
  --seed 42 --output work/triage-calibration-review
```

The required output directory must be new. Optional `--cases` and `--examples`
default to the included `cases.json` and `calibration.json`; `--seed` defaults
to 42. There are nine authored reports across three development cases: invalid
token, token expiry supported by a validator log, and absent evidence. Holdout
cases cannot be used for calibration.

The directory contains:

- `runs.jsonl`: authored reports in the baseline scorer's input format, with
  `purpose=judge_calibration` and explicit non-investigator origin. Preparation
  makes no model calls. The `completed` status means fixture assembly completed,
  not that a model generated a successful diagnosis.
- `review.json`: redacted evidence, proposed case expectations and candidate
  reports, with neutral stable IDs and shuffled ordering. Author notes and
  suggested quality labels are omitted. All ratings start pending/null. The
  source examples remain visible in Git; this is a review aid, not a claim of
  experimental blinding.

Have an SRE review the case expectations first. In the worksheet, record their
identity in `reviewer`, set each case `decision` to `accepted` or `revise`, and
add notes. A case marked `revise` is excluded from agreement comparisons; correct
its source expectations and prepare a new pack before accepting it. Changing
evidence, proposed expectations, report text or IDs inside an existing pack is
rejected during comparison.

For each report, fill both integer ratings (0–3), a rationale, and set
`review_status` to `reviewed`. Leave unfinished entries pending; retain them in
the file. Proposed anchors to discuss with the SRE:

| Rating | Evidence support | Diagnostic usefulness |
|---|---|---|
| 0 | Materially false or unsupported | Wrong or misleading |
| 1 | Mixed support and unsupported claims | Mostly repeats symptoms |
| 2 | Mostly supported, minor gaps | Useful finding with some omissions |
| 3 | Supported with appropriate limitations | Strongest justified finding and targeted next check |

Correct uncertainty can earn full credit on missing-evidence cases. Unnecessary
abstention loses usefulness credit when decisive evidence exists. A matching
symptom does not justify an invented deployment or underlying cause. These
anchors and example reports are proposals pending SRE validation.

## Judge the authored reports

Use the existing isolated, locked DeepEval project with a configured judge model:

```bash
DEEPEVAL_TELEMETRY_OPT_OUT=1 uv run --locked \
  --project tests/core/telemetry/evaluation/judge \
  python tests/core/telemetry/evaluation/judge/score.py \
  --runs work/triage-calibration-review/runs.jsonl \
  --output work/triage-calibration-review/scores.jsonl \
  --model gpt-4.1
```

The model is an example selector. This is the only live step in this calibration
workflow and consumes judge quota. It receives redacted incident evidence, the
proposed case rubric, and each report. It receives no human ratings or author
notes. Do not show judge results to the reviewer until their ratings are saved.
To assess judge variation, rescore the same pack to a new output file.

## Compare against human ratings

```bash
uv run python -m tests.core.telemetry.evaluation.calibration compare \
  --runs work/triage-calibration-review/runs.jsonl \
  --scores work/triage-calibration-review/scores.jsonl \
  --review work/triage-calibration-review/review.json \
  --output work/triage-calibration-review/comparison.json
```

This offline command binds both judgments to the same immutable input snapshot.
Only attributed, completed ratings for accepted case expectations count. It
compares report preferences **within each case and metric**; the human ordinal
scale is not treated as equivalent to DeepEval's normalized score scale.

Output includes comparable pairs, agreements, human ties, judge ties, missing
judge pairs and individual disagreements. Human ties are excluded from the
agreement denominator; judge ties disagree with a strict human preference.
With no eligible comparisons, agreement is `null`, never perfect. These small,
correlated example pairs do not establish statistical confidence or investigator
accuracy. The command does not mark source fixtures as SRE-reviewed.

## Produce the real baseline scorecard

Run the [baseline generation and scoring commands](README.md) on investigator
outputs, then:

```bash
uv run python -m tests.core.telemetry.evaluation.reporting \
  --runs work/triage-baseline.jsonl \
  --scores work/triage-baseline-scores.jsonl \
  --output work/triage-baseline-scorecard.json
```

The scorecard rejects authored calibration packs. It validates that score results
match their metric checkpoints and exact generation snapshot, then groups
results by failure class. When its hash matches the generation manifest, the
included case dataset identifies selected classes even if they have zero
recorded attempts. Use optional `--cases` to supply an original custom dataset;
an explicitly supplied dataset must match the recorded hash. If the default
dataset has changed, recorded attempts still identify known classes and all
remaining selected case IDs appear in an `unclassified` coverage bucket.

Every class includes expected, recorded and missing generation counts. No
recorded attempt means no duration or semantic score, rather than a zero-cost
success. The scorecard exposes generation failures, partial results,
missing attempts, unfinished judging, per-case metric variation and durations.
Generation contract failures are counted from every saved investigator result,
independently of whether judging ever reaches that attempt.
An interrupted score file retains completed metric checkpoints; absent metrics
remain missing rather than becoming zero or perfect scores. Stored summary
statistics are ignored and recomputed. Fixture review counts refer to unique
recorded cases, not repeat attempts.

All output files must be new; generated material stays under ignored `work/`.
Readers accept an incomplete final JSONL write while flagging it, and reject
damaged complete rows, duplicate score events, changed generation facts and
invalid metric values.

## Baseline v0 readiness

Before treating the baseline as useful measurement, SRE-review all incident
expectations, inspect human/judge disagreements, and collect repeated live
investigator results with a fixed model configuration and rubric. Record the
source hashes and missing/failing attempts. Unsupported-claim counts, useful
diagnosis counts and appropriate-abstention counts still require claim-level
SRE review; a semantic score is not a verified count of those outcomes.

This draft supplies no live score report, human rating or calibrated threshold.
The previous live investigator smoke was blocked by the environment proxy;
successful investigator and judge access remain prerequisites for collecting
real results. Ordinary pytest and CI remain offline.
