# ADR-030: Linked Repos: One Index per Repo, a User-Level Registry, Service Calls as the Connection

## Status: Draft (Part 2, phase 2.1 of 5)
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
- **No new MCP tool and no new tool parameter.** `status` lists each linked repo (name, path,
  file count, stale flag, skip reason). With no link, every result is the same as before.
- **`nexus-mcp-ci link <path>` indexes the target** when it has no index, or an index with no
  or an older `extractor_version`, or an index that the current model cannot use. It refuses
  to replace an index that was built for other folders. `link --refresh` indexes the changed
  files of every linked repo again and saves the graph after the metadata.
- **A linked store is read-only for the server.** Staleness is reported, not fixed. The
  person runs `link --refresh`.
- **Service calls are the connection** (phases 2.3 and 2.4): routes and HTTP client calls are
  extracted per repo, and a resolver makes bridge edges between graphs, with precision first
  (ADR-019). Each repo keeps its own graph, because `resolve_calls` rebuilds the CALLS edges
  of one graph and its unique-name tier must not see the names of another repo. Bridge edges
  live in memory and are not persisted.

## Consequences

- Linking an index made before this version builds it again one time: it has no
  `extractor_version` and no `repo_id`.
- A repo can have one index only. A repo that a person indexes with several roots cannot be
  linked until its `.nexus` folder is removed.
- Two processes can write to the registry at the same time. The write is atomic, but the
  second writer can overwrite the first one's change. A lock is not in scope.
- The BM25 engine creates its full-text index on the first search of a process
  (`ensure_fts_index(replace=True)`). That is a write. Phase 2.2 must not do this for a linked
  store: it must use an existing index or skip the keyword search for that store.
- The run of `link --refresh` while a server reads the same store is not locked. LanceDB
  readers see a consistent version, but the graph file is replaced. A lock between processes
  is in the "later" list of the plan.

## Alternatives considered

- **One merged index for all repos.** A rebuild for each change of the set, no stable
  identity, and the call resolver would see the names of every repo.
- **A link file inside `.nexus`.** Rejected for the trust rule above.
- **Package-import edges.** Later. Service calls need no package manifest and fit the
  stack where repos are deployed apart.
