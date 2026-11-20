# ADR-027: Stop Hook and Check Skill for the Plugin

## Status: Accepted
## Date: 2026-11-18

## Context

`nexus-mcp-ci check` (ADR-025, ADR-026) finds the callers, route users, edge function users and
tests that a diff did not update. It runs when a person or a CI job starts it. The agent that
made the change does not start it, so the finding reaches a reviewer after the work, not the
agent during the work.

The north star (CLAUDE.md) keeps a change only when it lowers the cost of a task and the
accuracy does not fall. A hook that speaks on each turn, or a result field on each call, adds
tokens to every session. So each part of Phase 4 had to earn its tokens.

## Decision

- **A `Stop` hook in the plugin** (`plugin/hooks/hooks.json`) runs
  `nexus-mcp-ci check --hook` when the agent ends a turn. The flag (`evaluate/check.py`,
  `hook_decision`) reads the hook JSON from stdin and is silent in each of these cases:
  - `stop_hook_active` is true (the hook already blocked this stop, so there is no loop);
  - `NEXUS_HOOK=off`;
  - there is no index, no git repository or no diff, or the check fails for any reason;
  - no finding has the severity `high`;
  - the same set of findings was shown before (a digest in `<index folder>/hook-last.json`).
  Otherwise it prints `{"decision": "block", "reason": ...}`. The reason is at most 6 findings,
  one line each, and 5 tests. The process always exits 0.
- **A quiet hook costs no tokens.** A clean turn adds no text to the session. Only a new
  `high` finding (a removed or changed symbol, a route that no longer serves a path, a dropped
  column) adds about 100 to 300 tokens, once, and the agent fixes a caller that it would have
  missed. A digest keeps the agent from hearing the same finding again and again when it
  decides that a caller stays.
- **The command ends in `|| true` and sends stderr to `/dev/null`.** The plugin pins
  `nexus-mcp-ci==X.Y.Z` (docs/RELEASING.md), and a release before this change has no `--hook`.
  Exit code 2 from an unknown flag would block the stop. The hook is a no-op until the pin has
  the flag. `bump_version.py` and `check_docs.py` keep the pin in step with the package.
- **A skill `check` that only the user starts** (`plugin/skills/check/SKILL.md`,
  `disable-model-invocation: true`). A skill that the model can pick costs its description in
  each session. This one costs nothing until `/nexus-mcp:check`.
- **No hook on `PreToolUse`.** A hook before each edit speaks on each edit, so it costs tokens
  on each turn, and the call graph answer for the symbol is one `graph` call away. The routing
  skill and the server instructions already say to call `graph` with `transitive=true` before a
  change to a shared symbol.
- **No change-context field in `find_symbol` or `graph`.** The candidate was an `affected_tests`
  field. `graph` already lists the callers by file, and tests are the files that match the test
  path rule, so the field repeats information that the agent has. It would add tokens and no
  facts. It stays in the backlog until a live run shows fewer turns.

## Alternatives considered

- **`PostToolUse` after each edit.** It runs `check` after each of many edits in a turn, with a
  stale picture of a change that is not finished. The end of the turn is the first moment when
  the change is whole.
- **Block on `medium` findings.** Many are fine (a caller that handles a new parameter by
  default). A hook that cries often teaches the agent to ignore it. `medium` and `low` stay in
  the CLI report and in the skill.
- **Write the finding to a file and not block.** The agent does not read a file that no one
  tells it to read.

## Consequences

- **Wall time.** Each turn end starts a process: about 1.2 seconds on a small project (measured
  in `tests/test_check_hook.py`, a clean start plus the check), more with `uvx` on a cold cache
  and more on a large diff. The live benchmark measures wall time but does not run hooks; the
  cost of the hook in wall time and its effect on missed call sites need the change-safety
  benchmark (Phase 5).
- **A lower bound.** The check is static, so a quiet hook does not prove that nothing breaks. The
  block text says that the check can be wrong about a dynamic call.
- **Branches.** On a feature branch the check compares with the merge base, so a `high`
  finding from an earlier commit of the branch is shown once, then held by the digest.
- **Claude Code only.** Hooks of other clients (Cursor, Gemini, Codex) are not added. The CLI
  flag is the same everywhere.
