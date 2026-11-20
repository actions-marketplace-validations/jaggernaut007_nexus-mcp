"""The join of boundary facts across linked repos (ADR-030, phase 2.4 reworked).

Inside one repo the boundary edges of ADR-026 (`boundary_resolver.py`) are the only edges: a
`fetch("/orders")` and the handler of `POST /orders` get a `DEPENDS_ON` edge. This module adds
the join between the graphs of the repos of a session. It reads the same facts: the
`boundary_uses` of a graph node (the client side) and the route providers of
`boundary_resolver.route_providers` (the server side). The extractor of facts is not
duplicated.

The rule is precision first, as in `call_resolver.py` (ADR-019). A client call gets an edge
only when exactly one route of the whole session fits at the first tier that has any
candidate, and that route lives in another repo:

- `exact` (strength 1.0): the method and the full path are equal. A client URL that starts
  with a base-URL variable (`GET {}/orders`) is read from its first `/`.
- `suffix` (strength 0.8): the path of a route equals the end of the client path, for a mount
  prefix that another file sets (`include_router(prefix="/api")`). The part that is cut off
  must have literal segments only: `/users/{}/orders` is a nested resource, not a prefix of
  `/orders`. A route with no literal segment (`/{}`) never fits this way.

A call whose route is in its own repo gets no edge here: ADR-026 already made it (an open
key, `GET {}/x`, gets none from ADR-026, so it gets no edge for an own-repo route). Two fitting
routes, in any repos, give no edge, and the next tier is not tried.

The edges live in a `ServiceBridge`, in memory. No graph holds them and none is saved with
them. The bridge is rebuilt when the linked repos are attached and after a reindex. Calls
and routes in test files are left out: a test often calls a route of its own project with a
test client, and that is no link between two services.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from nexus_mcp.core.graph_models import NodeType, UniversalNode
from nexus_mcp.engines.fusion import is_test_path
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.boundary_resolver import route_providers
from nexus_mcp.parsing.boundaries import KIND_ROUTE

logger = logging.getLogger(__name__)

PRIMARY = ""  # the repo key of the project of the session; a linked repo uses its name
TIER_EXACT = "exact"
TIER_SUFFIX = "suffix"
_STRENGTH = {TIER_EXACT: 1.0, TIER_SUFFIX: 0.8}

Key = Tuple[str, str]  # (repo key, node id)


@dataclass
class ServiceEdge:
    """One edge: a client node of a repo and the route handler of another repo."""

    client_repo: str
    client_id: str
    route_repo: str
    route_id: str
    tier: str
    via: str  # `GET /orders/{}`: the call, as the client wrote it
    route: str  # the route that serves it

    @property
    def strength(self) -> float:
        """The confidence of the edge, set by its tier: 1.0 for `exact`, 0.8 for `suffix`."""
        return _STRENGTH[self.tier]


@dataclass
class ServiceBridge:
    """The edges between the graphs of a session. In memory only."""

    edges: List[ServiceEdge] = field(default_factory=list)
    _into: Dict[Key, List[ServiceEdge]] = field(default_factory=lambda: defaultdict(list))
    _out: Dict[Key, List[ServiceEdge]] = field(default_factory=lambda: defaultdict(list))

    def add(self, edge: ServiceEdge) -> None:
        """Store `edge` and index it by the route it reaches and by the client that makes it."""
        self.edges.append(edge)
        self._into[(edge.route_repo, edge.route_id)].append(edge)
        self._out[(edge.client_repo, edge.client_id)].append(edge)

    def callers_of(self, repo: str, node_id: str) -> List[ServiceEdge]:
        """Edges that end at this route handler: the client nodes that call it."""
        return list(self._into.get((repo, node_id), ()))

    def callees_of(self, repo: str, node_id: str) -> List[ServiceEdge]:
        """Edges that start at this client node: the route handlers it calls."""
        return list(self._out.get((repo, node_id), ()))

    def __len__(self) -> int:
        return len(self.edges)

    def __bool__(self) -> bool:
        return bool(self.edges)


@dataclass
class _Route:
    repo: str
    node: UniversalNode
    method: str
    path: str


def _has_literal(path: str) -> bool:
    return any(part and part != "{}" for part in path.split("/"))


def _relative(file_path: str, roots: Sequence[Path]) -> str:
    """The path below the repo root: a folder above the root must not make a file a test."""
    for root in roots:
        try:
            return str(Path(file_path).relative_to(root))
        except ValueError:
            continue
    return file_path


def _suffixes(path: str) -> List[str]:
    """The proper ends of a path at a segment border, when the cut-off part is all literal.

    `/api/v1/orders` -> `/v1/orders`, `/orders`. `/users/{}/orders` -> none for `/orders`,
    because the part that would be cut (`/users/{}`) holds a parameter.
    """
    parts = [p for p in path.split("/") if p]
    out = []
    for i in range(1, len(parts)):
        if "{}" in parts[i - 1]:
            break
        if parts[i] == "{}":
            continue  # a mount prefix is followed by a literal segment, not a parameter
        out.append("/" + "/".join(parts[i:]))
    return out


def _client_path(path: str) -> str:
    """The path of a client key. A base-URL variable at the start (`{}/orders`) is dropped."""
    while path.startswith("{}"):
        path = path[2:]
    return path if path.startswith("/") else ""


class _RouteTable:
    def __init__(self) -> None:
        self.by_key: Dict[Tuple[str, str], List[_Route]] = defaultdict(list)

    def add(self, route: _Route) -> None:
        """Store `route` under its method and path."""
        self.by_key[(route.method, route.path)].append(route)


def _distinct(routes: Iterable[_Route], client: Key) -> List[_Route]:
    """One entry for each route node, and never the client node itself."""
    seen: Set[Key] = set()
    out = []
    for route in routes:
        key = (route.repo, route.node.id)
        if key == client or key in seen:
            continue
        seen.add(key)
        out.append(route)
    return out


def _match(
    table: _RouteTable, client: Key, method: str, path: str
) -> Optional[Tuple[_Route, str]]:
    exact = table.by_key.get((method, path), [])
    if exact:
        # A tier with any candidate ends the search, also when the only one is the client
        # itself (a proxy that calls the route it serves): the suffix tier must not run.
        fitting = _distinct(exact, client)
        return (fitting[0], TIER_EXACT) if len(fitting) == 1 else None
    ends = _distinct(
        (r for suffix in _suffixes(path) for r in table.by_key.get((method, suffix), [])),
        client,
    )
    return (ends[0], TIER_SUFFIX) if len(ends) == 1 else None


def resolve_services(
    graphs: Mapping[str, RustworkxCodeGraph],
    roots: Optional[Mapping[str, Sequence[Path]]] = None,
) -> ServiceBridge:
    """Match the client calls of every graph with the routes of the other graphs.

    `graphs` maps a repo key (`PRIMARY` for the project of the session) to its graph.
    `roots` maps the same keys to the repo roots, so that the test-file rule looks at the
    path inside the repo. Nothing is written to a graph.
    """
    roots = roots or {}
    table = _RouteTable()
    clients: List[Tuple[str, UniversalNode]] = []
    for repo, graph in graphs.items():
        repo_roots = roots.get(repo, ())
        for key, nodes in route_providers(graph, repo_roots).items():
            method, _, path = key.partition(" ")
            for node in nodes:
                if _has_literal(path) and not is_test_path(
                    _relative(node.location.file_path, repo_roots)
                ):
                    table.add(_Route(repo, node, method, path))
        with graph._lock:
            for node in list(graph.nodes.values()):
                if node.node_type != NodeType.FUNCTION or not node.metadata.get("boundary_uses"):
                    continue
                if not is_test_path(_relative(node.location.file_path, repo_roots)):
                    clients.append((repo, node))
    bridge = ServiceBridge()
    made: Set[Tuple[Key, Key]] = set()
    for repo, node in clients:
        for fact in node.metadata["boundary_uses"]:
            if fact.get("kind") != KIND_ROUTE:
                continue
            method, _, raw = str(fact.get("key", "")).partition(" ")
            path = _client_path(raw)
            if not method or not path or not _has_literal(path):
                continue
            found = _match(table, (repo, node.id), method, path)
            if found is None:
                continue
            route, tier = found
            if route.repo == repo:
                continue  # inside one repo ADR-026 makes the edge (none for an open key)
            ends = ((repo, node.id), (route.repo, route.node.id))
            if ends in made:
                continue
            made.add(ends)
            bridge.add(ServiceEdge(
                client_repo=repo, client_id=node.id,
                route_repo=route.repo, route_id=route.node.id,
                tier=tier, via=f"{method} {path}", route=f"{route.method} {route.path}",
            ))
    logger.info("Resolved %d service edges from %d client nodes", len(bridge), len(clients))
    return bridge
