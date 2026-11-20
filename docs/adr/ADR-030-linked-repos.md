# ADR-030: Linked Repos: One Index per Repo, a User-Level Registry, Service Calls as the Connection

## Status: Draft (Part 2, built in phases 2.1 to 2.5)
## Date: 2026-11-17

## Context

One store holds one set of roots today. A different set of roots makes a full rebuild. A
repo has no identity, and nothing connects a client call in one repo to the route that it
calls in another. Agents that work on a front end and a back end need both sides.

The owner decided on 2026-11-17:

- The connection between repos is the service call (HTTP, API), not a package import.
- Each repo has one index. Indexes are linked, not merged.
- A person links repos with a CLI command that writes a registry file.

## Decision

- **One index per repo.** Each repo keeps its store in `<repo>/.nexus`. The store gets a
  `repo_id` (uuid4, made one time and kept through every reindex, also after a rebuild of a
  corrupt index), a `repo_name` and an `extractor_version` in `index_metadata.json`.
- **A user-level registry, outside each repo.** `~/.nexus-mcp/links.json` (`NEXUS_LINKS_FILE`)
  maps the root of a project to its links: `{name, path, repo_id}`. It is not in `.nexus`
  because `core_api._stored_roots` does not trust the contents of `.nexus`: a clone or an
  archive can supply it. A link in a `.nexus` folder would let a repo choose what the server
  opens. The registry is written only by `nexus-mcp-ci link`, `unlink` and `link --refresh`,
  in one atomic step (a temporary file, then a rename).
- **The server attaches linked stores read-only.** The saved graph is opened with SQLite
  `mode=ro&immutable=1`: no pragma, no table creation, no `-wal` or `-shm` file. A write that
  runs at the same time is not seen, and a half-written file skips the link. The primary project stays in the fields of
  `SessionState`. Linked repos go in a new `state.linked` list of `LinkedRepo`. The attach
  runs at restore and after `index`. The server checks each linked store before it opens it,
  and trusts nothing in it: the roots in its metadata must be the folder of the registry, and
  its `repo_id` must be the one of the registry.
- **A link that cannot attach is skipped, with a reason**, and stays in the list so that
  `status` can show it: the folder is gone, there is no index, the metadata or the store is
  corrupt, the embedding model differs, the repo id changed, the index was built for another
  folder, or the saved graph is missing or older than the metadata. A skipped link never
  stops the primary project.
- **`search` and `find_symbol` look in the linked repos** (phase 2.2). `search` embeds the
  query one time (`embed_query`, then `query_vector`) and runs the vector and BM25 search in
  each attached store. It merges the vector lists by score and the BM25 lists by score, then
  uses the same RRF, test-file order and exact-name rule as before. The BM25 score is divided
  by the best score of its store; phase 2.5 changes how the lists merge (see below). A result
  of a linked repo has the
  repo name first in its path (`backend/src/app.py`). `find_symbol` resolves a name in the
  graph of each repo, the primary project first, and applies the same sort and the same cap
  (`MAX_MATCHES_PER_NAME`) to all matches. With `detail="full"` an entry has a `repo` field.
  The live-grep fallback of `search` still reads the primary roots only.
- **The BM25 engine of a linked store is read-only.** `IndexingPipeline(read_only=True)`
  gives `LanceDBBM25Engine(read_only=True)`: it never calls `create_fts_index`. It uses the
  full-text index that `link` made at index time. A store with no such index gives no keyword
  results, and the vector results remain.
- **A stale linked repo is reported, not fixed.** A `search` result in a changed file of a
  linked repo has no snippet and `changed_since_index`, and the `warning` names the repo and
  says `nexus-mcp-ci link --refresh`. A search never starts a reindex of a linked repo.
- **No new MCP tool and no new tool parameter.** `status` lists each linked repo (name, path,
  file count, stale flag, skip reason). With no link, every result is the same as before.
- **`nexus-mcp-ci link <path>` indexes the target** when it has no index, or an index with no
  or an older `extractor_version`, or an index that the current model cannot use. It refuses
  to replace an index that was built for other folders. `link --refresh` indexes the changed
  files of every linked repo again and saves the graph after the metadata.
- **A linked store is read-only for the server.** Staleness is reported, not fixed. The
  person runs `link --refresh`.
- **Service calls are the connection** (phases 2.3 and 2.4, reworked on top of ADR-026).
  There is one extractor of routes and client calls: the boundary facts of ADR-026
  (`parsing/boundaries.py`, `boundary_uses` and `boundary_serves` on graph nodes). The first
  build of phase 2.3 had a second extractor. It was removed. This ADR adds only what
  ADR-026 did not read:
  - the router prefix that the same file sets (`APIRouter(prefix=...)`,
    `Blueprint(url_prefix=...)`, `include_router(..., prefix=...)` in the same file);
  - NestJS controllers (`@Controller("orders")` with `@Get(":id")`);
  - Next.js app-router files (`app/api/orders/[id]/route.ts`, a function named as a verb),
    read by path in `boundary_resolver` as the edge functions of Supabase are;
  - a client URL that starts with a base-URL variable (`${BASE}/orders`) gets an open key,
    `GET {}/orders`. An open key equals no route key, so inside one repo it gives no edge;
  - `$fetch` and `ofetch` count as `fetch`.
  A URL with a literal host (`https://api.stripe.com/...`) still gives no fact.
  `EXTRACTOR_VERSION` is 2, so `link` builds an older store again.
- **Inside one repo, the boundary edges of ADR-026 (`DEPENDS_ON`) are the only edges.** The
  new reads above change some of its keys and add providers and users (a prefix is now part of
  the key; NestJS and Next.js providers; `$fetch` and `ofetch` users), so this version adds some
  in-repo edges and moves some keys. The
  join across repos (`indexing/service_resolver.py`) reads the same facts: the
  `boundary_uses` of one graph and the route providers of `boundary_resolver.route_providers`.
  It makes an edge only when exactly one route of the whole session fits, and that route is in
  another repo. Tier `exact` (method and path equal, strength 1.0), tier `suffix` (the route
  path ends the client path, for a mount prefix that another file sets; the part that is cut
  off has literal segments only, strength 0.8). Two fitting routes, in any repos, give no
  edge. Calls and routes in test files are left out. The edges live in a `ServiceBridge`
  (`state.bridge`), in memory: no graph holds them and none is saved with them. The bridge is
  built when the linked repos are attached and again after a background reindex. A failure
  leaves `state.bridge` empty and `graph` answers as it does with no link.
- **`graph` follows the bridge** (phase 2.4). For a symbol of the primary project the answer
  is the same as before, and one field is added when it is not empty: `linked_callers` or
  `linked_callees` (compact: `file -> ["name:line via GET /orders/{}"]`, with the repo name
  first in each linked path; `detail="full"`: nodes with `repo`, `via` and `resolution`), and
  with `transitive=True` `linked_impacted` (`file -> ["name via ..."]`). A symbol that only a
  linked repo defines is found too: its own callers or callees, and its bridge edges, with the
  repo name first in each path. No new tool and no new parameter. With no link no field is
  added.
- **The BM25 lists of the stores are scaled together** (phase 2.5). Each store divided its
  BM25 scores by its own best, so a weak linked match scored 1.0 and outranked the second
  result of the project (hit@1 of `shop_repo` with a link: 0.75, 0.75, 0.625, 0.625, 0.5, 0.75
  in six runs). `search` now divides the lists of all stores by the best raw score of all
  (`LanceDBBM25Engine.search(..., keep_raw=True)`, `_merge_bm25`); hit@1 with the link is 0.75
  in six of six runs. With no link the path is the same as before. Trade-off: two of four
  queries about the linked repo that are ambiguous with the project now go to the project
  (the linked side found a linked file first in a mean of 0.79 before and 0.54 after). A raw
  BM25 score grows with the size of its store (0.85 for one chunk, 6.4 among 10, 13.0 among
  100, 20.0 among 1000), so a large linked repo can still outrank a small project. A
  correction by the size of the store lost a project result in one run of six and was not
  adopted. Whether the project must win ambiguous queries is the decision of the owner.
- **Evals** (phase 2.5). `evals/linked/run.py` scores the edges of the join against hand
  labels on the fixture `tests/fixtures/linked` (a FastAPI backend and a TypeScript
  frontend with decoys): precision 1.0, recall 0.9 (one labelled miss: a path in a table).
  The gate is precision of 0.9 or more. `evals/linked/with_link.py` measures hit@1 of `search`
  with and without a link. Not done: hand labels for a public full-stack project (it needs a
  clone and a named project).

## Consequences

- Linking an index made before this version builds it again one time: it has no
  `extractor_version` and no `repo_id`.
- A repo can have one index only. A repo that a person indexes with several roots cannot be
  linked until its `.nexus` folder is removed.
- Two processes can write to the registry at the same time. The write is atomic, but the
  second writer can overwrite the first one's change. A lock is not in scope.
- The BM25 engine of a normal store creates its full-text index on the first search of a
  process (`ensure_fts_index(replace=True)`). That is a write. A linked store uses a read-only
  engine (phase 2.2), so a search never changes it. A linked store without a full-text index
  gives vector results only, until `link --refresh` runs.
- A link name is the first part of a result path. A name equal to a top-level folder of the
  primary project makes two paths look the same. Use `--name` to choose another one.
- An open client key (`GET {}/orders`) is read from its first `/`: an equal route path gives
  tier `exact`, a route whose prefix another file sets gives tier `suffix` (0.8). The base URL
  can be a third party that is held in a variable (`${STRIPE}/v1/customers`): it is not known, so
  such a call can match a linked route of the same path. A call with an own-repo route gets no
  edge.
- `EXTRACTOR_VERSION` is written on every metadata save, also after an incremental reindex, so an
  index with older facts can claim version 2. `link --refresh` on a changed repo is the sure way
  to get new facts.
- A Next.js route needs one `app` folder below the repo root; a path with two gives no key.
- A router that two files mount with different
  prefixes keeps one path. Express mounts in another file, Django, Go, Java, C#, PHP and gRPC
  are not read.
- Each search of a project with links reads every attached store, so a search costs a little
  more time for each link. With no link the code path is the same as before.
- The run of `link --refresh` while a server reads the same store is not locked. LanceDB
  readers see a consistent version, but the graph file is replaced. A lock between processes
  is in the "later" list of the plan.

## Alternatives considered

- **One merged index for all repos.** A rebuild for each change of the set, no stable
  identity, and the call resolver would see the names of every repo.
- **A link file inside `.nexus`.** Rejected for the trust rule above.
- **Package-import edges.** Later. Service calls need no package manifest and fit the
  stack where repos are deployed apart.
