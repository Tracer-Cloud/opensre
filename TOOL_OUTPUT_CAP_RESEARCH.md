# Codex tool-output policy parity

Inspected and implemented on 2026-10-09. OpenSRE baseline: `2082b19b15ec82193a0d976820f84024f9ca2e7d`.

The default text-output policy now follows Codex: **10,000 approximate tokens, estimated as four UTF-8 bytes per token**, with equal beginning/end retention. This is a 40,000-byte retained-text budget, independent of line count. Truncation markers sit outside that budget and identify the omitted approximate tokens. Splits preserve complete Unicode characters.

The supplied comparison row had no column headings. Its 2,000-line / 50 KiB entries appear to describe OpenCode and Pi; that attribution is an inference. They do not describe the current bundled Codex model default. Codex's unknown-model fallback is separately 10,000 **bytes**. [Codex model catalog](https://github.com/openai/codex/blob/main/codex-rs/models-manager/models.json), [model fallback](https://github.com/openai/codex/blob/main/codex-rs/models-manager/src/model_info.rs), [official truncation guidance](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide#tool-response-truncation).

## Implemented comparison

| Boundary | Previous OpenSRE behavior | OpenSRE after this change / Codex default policy |
| --- | --- | --- |
| Provider-visible text | No shared cap; shell kept the first 24,000 characters per stream | 10,000 approximate tokens / 40,000 retained UTF-8 bytes, beginning and end |
| Recorded tool history | 8,000 characters per result | Policy multiplied by 1.2: 48,000 retained bytes for serialization overhead |
| Shell capture | Lists accumulated both streams until command completion | Shared 1 MiB beginning/end buffer; omitted bytes counted |
| Audit result persistence | First 2,000 characters | 64 KiB beginning/end byte budget, with omitted-character marker |
| Summarizer input | Additional 2,000-character cut per recorded result | Uses the recorded observation without another per-result cut |

Codex references: [text truncation](https://github.com/openai/codex/blob/main/codex-rs/utils/string/src/truncate.rs), [history allowance](https://github.com/openai/codex/blob/main/codex-rs/core/src/context_manager/history.rs), [exec capture](https://github.com/openai/codex/blob/main/codex-rs/core/src/unified_exec/mod.rs), [capture buffer](https://github.com/openai/codex/blob/main/codex-rs/core/src/unified_exec/head_tail_buffer.rs), [rollout persistence](https://github.com/openai/codex/blob/main/codex-rs/rollout/src/policy.rs).

The local first-party Codex checkout used for exact algorithm comparison is [commit b43de77679faa53b3bc39d1b72441b24d9d8f428](https://github.com/openai/codex/tree/b43de77679faa53b3bc39d1b72441b24d9d8f428), dated 2026-08-11. Mutable online source links were checked on the research date. These establish source policy, rather than the exact version of the installed desktop binary.

## OpenSRE wiring

The policy lives in [core/tool/output.py](/Users/janvincentfranciszek/opensre/core/tool/output.py), with shared budgets in [config/constants/tool_output.py](/Users/janvincentfranciszek/opensre/config/constants/tool_output.py). Ordinary tool results are bounded when converted into provider content, leaving their original runtime content intact. Multiple text blocks share one beginning/end budget, so a final diagnostic survives even when it occupies a separate block; non-text blocks remain present.

Shell results apply the body budget once and keep exit status, timeout, and cancellation metadata outside it, as Codex's shell response does. A combined observation retains stdout and stderr, including stderr from successful commands. It uses stable stdout-then-stderr order so pipe-reader scheduling cannot move a short warning into the omitted middle. The underlying [capture buffer](/Users/janvincentfranciszek/opensre/tools/interactive_shell/shell/output_capture.py) remains bounded while readers drain both pipes. Readers consume available byte chunks with incremental UTF-8 decoding, including partial output before EOF.

[Structured history](/Users/janvincentfranciszek/opensre/core/agent_harness/turns/structured_history.py) uses the 20% serialization allowance: exactly 48,000 bytes by default. For unusually small environment overrides, OpenSRE allows at least 256 bytes beyond the body to preserve already-observed status and omission markers; that minimum is an OpenSRE refinement. [Compaction input](/Users/janvincentfranciszek/opensre/core/agent_harness/turns/transcript_compaction.py) receives those recorded results without the former 2,000-character cut. [WAL recording](/Users/janvincentfranciszek/opensre/core/agent_harness/turns/wal_recorder.py) independently uses the 64 KiB persistence budget. The prior assertion that session logs always contain full output has been corrected.

Configuration:

- `OPENSRE_TOOL_OUTPUT_TOKEN_LIMIT` changes the shared approximate-token limit; default `10000`.
- `shell_run.max_output_tokens` can request a smaller budget, bounded by the shared policy.
- `OPENSRE_HISTORY_TOOL_RESULT_CHARS` remains an explicit optional additional replay cap. Its former implicit `8000` default is removed. Leave it unset for parity.

## Reliability and scope

The baseline probes reproduced three evidence losses: final shell diagnostics disappeared before first observation; a middle run ID disappeared between turns; and a fact present in history disappeared before reaching the summarizer. Regression tests now exercise these boundaries, exact Unicode truncation, shared block budgeting, bounded shell capture, and persisted head/tail retention. They verify evidence preservation; they do not quantify model answer accuracy.

Parity covers the default output budgets and truncation algorithms. OpenSRE retains its own context-window budgeting, eviction order, aggregate summarizer input limit, and compaction scheduling. Codex also permits model-specific truncation policies; OpenSRE uses the documented shared default and an environment override.

Neither the inspected Codex exec path nor this implementation guarantees a complete saved raw-output file. Capture and persistence are bounded. Previously omitted text cannot be recovered merely by enlarging the history cap, and changing the defaults does not expand already-recorded results. Redirecting an important command to a file remains a separate workflow.

## Validation

- All eight shared quality checks passed: lint, formatting, strict imports, registry checks, contracts, and product type checking. The final lint correction was verified by rerunning lint.
- Mapped suite: **6,816 passed, 47 skipped, four live LLM tests deselected, one expected failure**. Run with temporary OpenSRE storage and localhost access; no live model service calls were required for this successful run.
- Final focused suite after the token-rounding and partial-pipe refinements: **87 passed, 11 Windows tests skipped**. It exercises the real two-turn dispatch path, real shell processes, split UTF-8 across pipe reads, summarizer input, and JSONL WAL persistence.
- Direct type checking of the new policy, WAL, and shell test files passed; the working diff passes whitespace checks.

The first mapped run exposed the shell stream-order race, which was fixed. Its remaining failures came from four live LLM connection attempts, localhost socket restrictions, and a scheduler lock in the developer's home directory. The successful mapped run used isolated OpenSRE storage, enabled localhost access, and excluded the live LLM marker. Follow-up CI fixes preserved in-memory text-stream compatibility and updated native Windows expectations; the platform CI job passed. A final regression pins replay to exactly 48,000 bytes by default.
