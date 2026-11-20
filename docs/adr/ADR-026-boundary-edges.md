# ADR-026: Boundary Edges

## Status: Accepted
## Date: 2026-11-17

## Context

A call graph sees a function call a function. A TypeScript and Supabase code base breaks in
other places. `supabase.functions.invoke("send-receipt")` runs the edge function in
`supabase/functions/send-receipt/`. `fetch("/orders", {method: "POST"})` reaches a Python
handler. `.from("orders")` reads a table that a migration defines. The two sides share a name
and never a call, so `graph` and `check` found nothing, and these are the changes that
break the pipeline of a customer (the first forward-deployed customer named this pain).

## Decision

Read the name of each boundary on both sides, and join the sides with a `DEPENDS_ON` edge from
the user to the provider.

- **Facts** (`parsing/boundaries.py`, `parsing/sql_boundaries.py`). The ast-grep parser stores
  `boundary_uses` and `boundary_serves` on function nodes, and on the `<top-level>` node for
  the calls outside every function. A fact has `kind`, `key` and `line`.
  - `function`: `functions.invoke("name")`, and `fetch(".../functions/v1/name")`.
  - `route`: key `METHOD /path`, with every parameter segment as `{}`. Users: `fetch`, and
    `axios`, `requests`, `httpx` or a test `client` with a verb. Providers: Python decorators
    (`@app.get`, `@router.post`, `@app.route(..., methods=[...])`) and Express-style
    `app.post("/x", handler)`.
  - `table`: `.from("x")` in JS/TS, `.table("x")` and `.from_("x")` in Python.
- **SQL.** `.sql` is a supported extension. There is no grammar. A small reader finds `create`,
  `alter` and `drop` of tables and views, with line spans and the column names of a
  `create table`. Each statement is a `CLASS` node named after the table. Only `create` is a
  provider (`boundary_defines`). An `alter` or a `drop` is `boundary_touches`, with a
  `destructive` flag for a drop or a rename. The tree-sitter parser returns an empty result
  for `.sql`, so SQL adds no search chunks and no parse error.
- **Providers by path.** `supabase/functions/<name>/index.<ts|js>` provides the function
  `<name>`: the default export, or the `<top-level>` node when the file has none (`Deno.serve`).
  `_shared` folders provide nothing.
- **Resolution** (`indexing/boundary_resolver.py`), at the end of `resolve_calls`, so all four
  index paths and the check rebuild the edges. Precision first: an edge exists only when
  exactly one node provides the key. A key that two nodes serve, a URL with a host, a path
  that is not a string literal and a table that no `create` defines give no edge. A route
  handler named in `app.post("/x", handler)` must resolve to one function.
- **`check`** (`evaluate/check.py`). For each changed symbol the check finds the users by
  edge and by key, and reports `boundary_untouched` (the user's file is not in the diff) or
  `boundary_lines_unchanged`. The severity is `high` when the provider is removed, serves other
  keys, changes its parameters, drops columns, or the statement is a destructive `alter` or
  `drop`; else `medium`. The key lookup covers a new migration, which has no edge yet. Tests
  that reach a user, through calls, fixtures or boundaries, are in `affected_tests`.
  A decorator line now belongs to its function (`decorated_from`), so a changed route path maps
  to the handler.
- **`graph`** (`core_api.py`). A `callers` result gets `boundary_callers` (route, function or
  table, with the users by file) when users exist, at most 20. The field is absent otherwise,
  so it costs no tokens in the common case. `transitive=true` also follows boundary edges.
  There is no new tool and no new parameter.

## Alternatives considered

- **A new `boundaries` MCP tool.** Each tool costs prompt tokens on every turn. The data fits
  the existing `graph` result.
- **Link by regular expression over the text of every file.** It has no provider check, so
  `Array.from("abc")` and `cache.get("/x")` would give edges. The join on a real provider
  removes most false uses.
- **A SQL grammar.** The statement head is enough. A grammar adds a dependency and symbols
  that nobody searches for.
- **Edges to every candidate provider.** A missing edge is safer than a wrong one, as in
  ADR-019.

## Consequences

- **Lower bound.** A computed URL (`BASE + "/orders"`), a route built in a loop, an ORM query,
  `supabase.rpc(...)` and a client that is not named like one are not seen. The seeded set
  keeps a computed URL as a miss.
- `.sql` files are now in the index: more files, no chunks. They reach `analyze` as `CLASS`
  nodes, so a table counts as a class in `map` counts.
- Not covered yet: LLM tool declarations to handlers, Next.js file routes, `router.use`
  prefixes, a schema-qualified table that two schemas share, and an `alter` that renames a
  column (it is `destructive`, but the old name is not tracked).
- **Numbers** (offline, 2026-11-17). `evals/impact/boundary_cases.yaml`, 6 cases: recall 0.889
  and precision 1.000; with the boundary findings switched off the same set gives recall 0.000.
  `evals/impact/cases.yaml` is unchanged (recall 0.867, precision 1.000).
