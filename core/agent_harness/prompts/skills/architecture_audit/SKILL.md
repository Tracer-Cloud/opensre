---
name: architecture-audit
description: >-
  Architecture audit / layering scan of a repo with AGENT SCAN, heuristic
  passes, and a filled REPORT TEMPLATE
metadata:
  owner: Tracer Team
  usecases:
    - Architecture audit or layering review of a repository with a filled report
    - Structural summary of a codebase: hotspots, debt themes, refactor sequencing
    - Finding oversized files, shims, and layering violations
  requires:
    - Shell access to clone or read the repository
    - Write access to ~/.opensre for the saved observations
  type: audit
  version: "1.0"
---
══════════════════════════════════════════════════════════
ARCHITECTURE AUDIT SKILL — interactive-shell action agent:
══════════════════════════════════════════════════════════

WHEN TO USE (call this skill when the user ask matches any of these):
- Architecture audit / architecture review / architecture violations
- Structural summary of a codebase (what it does, hotspots, debt themes,
  recommended refactor sequencing) — not a one-line README paraphrase
- Phrases like: "audit Tracer-Cloud/opensre", "find architecture issues",
  "summarize this repo's architecture", "what's wrong with the layering",
  "find huge files / shims", "architecture report for owner/repo"

Do NOT use this skill for: live incident RCA, metric/log queries, deploying,
or ordinary chat that only needs a short verbal overview with no scan.

HARD RULES (violating any = failed turn):
- Never end the turn with shell_run as the last tool — final reply is the
  filled REPORT TEMPLATE (exact headings; `- none` for empty lists).
- Every shell_run in this skill MUST pass quiet=true (hide $ / stdout in the
  terminal; results still return to you).
- Order: AGENT SCAN before heuristics (≤3 shell_run; use discovered roots,
  do not assume source/include) → 4 separate heuristic shell_run passes →
  cleanup → architecture_save_observations (passes 3–6 only; path
  ~/.opensre/{session_id}/{repo_name}-architecture-audit-{uuid}.md) →
  no-tool report.
- Cap heuristic stdout (about 15–25 lines); You write each bash command; leave
  no clone on disk.
  Budget: clone + ≤3 agent-scan shell_run + 4 heuristic shell passes + cleanup
  + save observations + final report.

STEP LABELING RULES (UX) — this skill hides its shell output (quiet=true), so
the pinned plan overlay is the user's progress signal (Droid-style):
- Call `update_plan` **once at the start** of the run with the 9 compact-
  sequence steps below (pending). Mark the first step `in_progress`.
- Before starting each next step's tool calls, call `update_plan` again:
  mark the finished step `completed`, the next one `in_progress`. Never leave
  the plan idle with every step still pending.
- Do **not** dump `### [n/9]` headers into the transcript — the overlay already
  shows `Plan · n/m`. One short status sentence per step is enough.
- After the final report is ready, call `update_plan` with every step
  `completed` (or leave the last step completed from the prior update).

Compact sequence:
1) architecture_clone_repo(owner, repo, ref?)
   → workspace_root = <system-temp>/opensre/workspace
   (If already in the target checkout and no owner/repo named, skip clone and
   use cwd as workspace_root; then skip cleanup.)

2) AGENT SCAN — orient on the repo before heuristics (max 3 shell_run,
   each with quiet=true)
   Purpose: learn enough layout/docs context that later passes hit the right
   trees — not to invent a parallel findings dump.
   From workspace_root (repo root), discover top-level packages and where
   source vs public headers vs API/schema trees live. Prefer CONTEXT.md and
   ADRs in areas you touch; if missing, fall back to AGENTS.md,
   ARCHITECTURE.md, DECISIONS.md, docs/adr/, CONTRIBUTING.md layout notes,
   or nearest equivalents. Note "none found" when absent.
   Explore organically (≤3 shell_run total) and note early friction that
   should steer later probes:
   - Where does understanding one concept require bouncing between many
     small modules?
   - Where are modules shallow — interface nearly as complex as the
     implementation?
   - Where have pure functions been extracted just for testability, but
     the real bugs hide in how they're called (no locality)?
   - Where do tightly-coupled modules leak across their seams?
   - Which parts are untested, or hard to test through their current
     interface?
   Apply the deletion test to anything you suspect is shallow: would
   deleting it concentrate complexity, or just move it? A "yes,
   concentrates" is the signal you want.
   Carry the discovered roots/contracts into steps 3–6.

3) shell_run(command=..., quiet=true) — IMPORT pass
   Using AGENT SCAN layout/docs, discover the repo's stated layer/module
   import contract from layout + docs (AGENTS.md, ARCHITECTURE.md,
   CONTRIBUTING.md, build files, package maps), then gather evidence of
   cross-boundary imports that contradict that contract. Prefer the target
   repo's own rules. Treat composition roots / intentional wiring as allowed
   when the docs imply it; do not invent a stricter graph. Cap rows.

4) shell_run(command=..., quiet=true) — PLACEMENT pass
   Using AGENT SCAN layout, discover the repo's package/module placement
   contract from top-level layout, build/module definition files
   (settings.gradle, go.mod, Cargo.toml, pyproject.toml, package.json
   workspaces, Bazel/Pants/Nx, etc.), and AGENTS-style docs. Report only
   placements that contradict those contracts, with paths + the rule they
   break. Cap rows.

5) shell_run(command=..., quiet=true) — SIZE pass
   Decide what "large" means for this repo/request. Prefer a threshold the
   user named; otherwise choose a sensible bar from context (top outliers,
   percentile, or a justified line-count cutoff). State the chosen definition
   in the report. Scan source files of ANY language (e.g. .py, .go, .ts,
   .tsx, .js, .jsx, .java, .rs, .rb, .php, .cs, .kt, .swift, .c, .cc, .cpp,
   .h, .hpp, .scala, .sh) — do NOT limit to Python and do NOT skip non-Python
   sources. Prefer primary source roots discovered in AGENT SCAN; skip only
   noise dirs: tests, docs, examples, caches, .venv, node_modules, dist,
   build, vendor lock dirs, and binary/media assets (images, fonts,
   lockfiles, generated minified bundles). Cap rows.

6) shell_run(command=..., quiet=true) — SHIM pass
   Lightweight heuristic for compatibility / re-export / facade modules across
   languages, scoped to roots from AGENT SCAN. Distinguish deliberate public
   API entrypoints (keep as evidence, do not treat as debt by default) from
   thin leftover forwarding modules. Keep output short (cap rows).

7) architecture_cleanup_repo()  ← required next tool after step 6
   (Skip when step 1 used cwd and did not clone.)

8) architecture_save_observations(repo_name, observations)
   ← required after cleanup (or after shim when cleanup was skipped),
   before the final no-tool report. Ending the turn before this call skips it.
   `observations` = the complete markdown list of findings from passes 3–6
   (raw heuristic evidence).
   `repo_name` = the repo slug (e.g. opensre or owner/repo).

9) Final NO-TOOL reply: fill
   `core/agent_harness/prompts/skills/architecture_audit/architecture_audit_report.md`
   from passes 3–6 grounded in AGENT SCAN context. Summarize; never paste huge
   raw dumps. Invent Recommended sequencing yourself — calibrate to the repo's
   stated contract, not a generic "delete every cross-module edge" story. Propose
   tasks; never auto-apply fixes. File GitHub issues only after approval.
