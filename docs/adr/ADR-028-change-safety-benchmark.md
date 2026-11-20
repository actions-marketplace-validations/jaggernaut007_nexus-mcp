# ADR-028: Change-Safety Benchmark

## Status: Accepted
## Date: 2026-11-18

## Context

The north star (CLAUDE.md) has three parts of quality. The first two exist: hit@1 and the
answer score (find the right code), and the impact set of `check` (see what a change touches).
The third is missing: does an agent that has Nexus-MCP make a change and leave fewer call
sites behind, and fewer tests that fail? The first customer named this pain: an API change that
breaks the pipeline, and nobody can review 10 to 20 changed files.

The existing live benchmark (`benchmarks/runner.py`) cannot measure it. It is read-only: the
agent answers a question, and a script scores the words of the answer.

## Decision

A change task type in `benchmarks/change_safety/`.

- **A task** (`tasks.yaml`) is a tiny project, a change request, and facts for a score with no
  model: a `test_command`, hidden `acceptance` tests, a list of `sites` (each a file and the
  regex that must be gone), a `reference` solution and a `partial` solution (the definition
  and the obvious caller, the typical mistake). The pilot set has four tasks: a new required
  parameter (Python), a renamed route (Python, boundary), a renamed function (TypeScript), and
  a changed edge function response (TypeScript, boundary). One site is a computed name that no
  static tool sees (`unseen`); it stays in the set.
- **The score** (`score.py`) copies the project, lets the agent edit it, then writes the
  hidden tests, runs the test command and checks the sites. Two numbers: `missed_sites` and
  `tests_failed`. A change is `safe` when both are zero. TypeScript tests run with node's own
  type stripping (node 23.6 or newer): no npm install, no compiler.
- **The tasks prove themselves offline** (`tests/test_change_safety.py`). For each task the start
  state passes its visible tests and fails the hidden ones, the reference solution is safe, and
  the partial solution is not. A scorer that cannot tell these apart would make a live run
  meaningless. These tests run in the normal suite and cost nothing.
- **The live runner** (`run.py`) reuses `benchmarks/conditions.py` (the same conditions, the
  same isolation, the same `dontAsk` mode and explicit tool list, no bypass flag). The agent gets
  Read, Grep, Glob, Edit, Write and Bash for the test command only, in every condition. The
  `nexus` condition gets an index of the copy before the run, outside the measured time. The
  runner is a dry run unless `--go` is given, because a run costs money.
- **The check as an offline signal** (`tests/test_change_safety_check.py`). For each partial
  solution, `nexus-mcp-ci check` must name every left-behind site that a static tool can see.
  It does, for all four tasks. This is a floor for what a tool-using agent could know, not
  proof that an agent uses it.

## Alternatives considered

- **An LLM judge for the change.** It costs tokens, drifts, and argues. Tests and regexes do
  not.
- **Real repositories** (Django, Vite). The ground truth of a change in a large repository is
  slow to write and to check. The pilot uses small projects so that every site is known. A
  larger set from real commits is a later step, with the same scorer.
- **Edit the tasks until Nexus-MCP wins.** No. The set keeps a site that no static tool sees and
  an edge function change that the check rates only `medium`.

## Consequences

- **Not run live.** The cost, wall time and safe rate of a real agent are not measured. The
  plan is 24 runs (4 tasks, 2 conditions, 3 reps), at most $36 by the budget cap and likely
  $8 to $15. It needs the owner's go-ahead for the spend (`--go`).
- **A found gap, fixed.** Building the offline signal showed that TypeScript imports with an
  extension (`./cart.ts` for Node and Deno, `./cart.js` for TypeScript with NodeNext) gave no
  call edge, so `check` found the renamed function only by text search at `medium`.
  `call_resolver._relative_targets` now strips a script extension (`.ts`, `.tsx`, `.mts`,
  `.cts`, `.js`, `.jsx`, `.mjs`, `.cjs`). `./cart.json` is not stripped. The rule has
  tests that failed before the fix. Impact set numbers did not change (recall 0.867, precision
  1.000).
- **Small projects.** Every task fits in a few files, so a baseline agent with Grep may do as
  well as one with Nexus-MCP. That is a valid result; the report shows each task.
- **Pilot size.** Four tasks and 3 reps give a rough rate, not a published claim. Grow the set
  before a number goes into the README.
- **The Stop hook** (ADR-027) is not a condition yet. `nexus-plugin` would load it, but the
  pinned release has no `--hook` flag. Add the condition after the release.
