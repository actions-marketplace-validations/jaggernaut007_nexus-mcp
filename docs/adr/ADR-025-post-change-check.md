# ADR-025: Post-change check and stable symbol keys

## Status: Accepted
## Date: 2026-11-17

## Context

The north star (2026-11-17) is high-quality coding changes, at a lower cost, in faster
sessions. An agent that edits a shared symbol can leave callers behind, and a reviewer of 10
to 20 changed files cannot find them. The call graph already knows the callers. Nothing
compared a diff with them.

Four facts in this codebase decide the design:

1. A graph node id is random on each index run, so no id can name a symbol across runs.
2. Each visible MCP tool costs prompt tokens on every turn (ADR-017). A check that runs
   after the edit does not need to be a tool.
3. The index is stored as `graph.db` (SQLite). A CLI can read it without the embedding model.
4. Call edges are static and name-based (ADR-019): every answer is a lower bound.

## Decision

### The check is a CLI command

`nexus-mcp-ci check` runs outside the model. It adds no tokens to a session.

1. Read `git diff --unified=0 --no-renames` against the base: the merge base with the
   default branch, or `--base`. Untracked files count as added. `--staged` checks the
   index against HEAD.
2. Load the stored graph twice. The *before* graph has the changed files parsed from
   `git show <base>:<path>`. The *after* graph has them parsed from the working tree.
   `AstGrepParser.parse_source` parses text that is not on disk.
3. Map each changed line to the innermost function or class node that holds it. The
   `<top-level>` node counts only when no other node does. A symbol is *modified*,
   *added* or *removed* by its key in the two graphs. A changed parameter list or return
   type (`signature_of`, the same code for Python and TypeScript) is a *signature change*.
4. Take callers from the *before* graph, so a removed or renamed symbol still has them.
   A caller that the diff also changed is not a finding. A caller in a changed file that
   was not itself changed is `caller_lines_unchanged`. A caller in an untouched file is
   `untouched_caller`. Severity is high for a removed symbol or a signature change, and
   medium for a body change. Transitive callers (follow `CALLS` and `USES`) are low, and
   only for high-risk symbols. A whole-word text search adds uses that have no call edge
   (a registry, a string) for removed or re-signed names of 4 characters or more.
5. List the tests that the transitive callers and references reach (`is_test_path`).
6. Print markdown (a pull-request comment) or JSON. Exit 0 clean, 1 when a finding reaches
   `--fail-on` (default `high`), 2 for a usage or git error, 3 for no index, an index of
   another folder, or more than 200 stale files.

`nexus-mcp-ci index` builds the index without an agent, so `check` works in a script or in
CI.

### Stable keys

`core/keys.py` gives `<file>::<type>::<qualified name>`. `file` is relative to the project
with `/` separators. A method is `Class.method`. A repeated key gets `#1`, `#2` by first
line. A rename and a move between files are new keys on purpose. The module has no import
of the graph engine or of LanceDB. The JSON of `check` uses keys, never node ids.

### Fixtures are edges

A pytest test names a fixture by parameter, so no call exists. `resolve_fixtures` adds a
`USES` edge to the nearest fixture of that name: the same file, then `conftest.py` from the
test's folder up to the root. `graph()` still follows `CALLS` only, so its output does not
change. The check follows `CALLS` and `USES`.

### Not done

- No new MCP tool and no schema parameter. An `affected_tests` field in `graph()` is not
  added until its token cost is measured in the live benchmark.
- `--staged` reads the working tree for the *after* side, not the index.
- A caller counts as updated when any line of it changed, not only its call site.

## Consequences

- A change is checked in seconds, offline, for Python and TypeScript (and any language
  with call edges). The result is a lower bound: dynamic calls, callbacks and computed
  names are not seen.
- The seeded breakage set (`python -m evals.impact.run`) measures the check. First run:
  recall 0.867, precision 1.000, tests recall 1.000. Its misses stay in the set.
- Known gap: a method call whose receiver is named like an imported module file
  (`cart.add_item()` after `from cart import Cart`) gets no edge, because the resolver
  treats `cart` as a module. The text search names the file instead.
