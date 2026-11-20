"""Boundary edges: the join of what a node uses with what another node serves (ADR-026).

`parsing/boundaries.py` leaves `boundary_uses` and `boundary_serves` facts on the nodes.
This pass links each user to the node that serves the same key:

- `function`: `supabase.functions.invoke("send-receipt")` -> the code of
  `supabase/functions/send-receipt/index.ts`.
- `route`: `fetch("/orders", {method: "POST"})` -> the handler of `POST /orders`.
- `table`: `.from("orders")` -> the node of the SQL table `orders` (when the index holds one).

The edge has the type `DEPENDS_ON` and runs from the user to the provider, as a call does.
Precision comes first: a key that two nodes serve gets no edge. The pass rebuilds all
boundary edges, like `resolve_calls`, and runs at the end of it.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalNode,
    UniversalRelationship,
)
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.indexing.call_resolver import _Index, _resolve_one
from nexus_mcp.parsing.boundaries import KIND_FUNCTION

logger = logging.getLogger(__name__)

BOUNDARY_STRENGTH = 0.8
# `supabase/functions/<name>/index.ts`; `_shared` holds code that no request reaches.
_EDGE_FUNCTION = re.compile(r"(?:^|/)supabase/functions/([^/_][^/]*)/index\.(?:[cm]?[jt]sx?)$")

Key = Tuple[str, str]


def edge_function_name(file_path: str) -> str:
    """The name of the edge function that a file is the entry of, or ""."""
    found = _EDGE_FUNCTION.search(file_path.replace("\\", "/"))
    return found.group(1) if found else ""


def _providers(graph: RustworkxCodeGraph, index: _Index) -> Dict[Key, List[UniversalNode]]:
    """Every node that serves a key, by (kind, key)."""
    served: Dict[Key, List[UniversalNode]] = defaultdict(list)

    def add(key: Key, node: UniversalNode) -> None:
        if all(n.id != node.id for n in served[key]):
            served[key].append(node)

    for node in graph.nodes.values():
        if node.node_type == NodeType.MODULE:
            name = edge_function_name(node.location.file_path)
            if not name:
                continue
            path = node.location.file_path
            entry = index.default_export.get(path) or index.by_name_file.get(("<top-level>", path))
            for target in (entry or [])[:1]:
                add((KIND_FUNCTION, name), target)
            continue
        if node.node_type not in (NodeType.FUNCTION, NodeType.CLASS):
            continue
        for fact in node.metadata.get("boundary_serves", ()):
            target = node
            handler = fact.get("handler")
            if handler:
                (targets, _tier), _unknown = _resolve_one(handler, node, index)
                if len(targets) != 1:
                    continue  # a handler that cannot be named gives no provider
                target = targets[0]
            add((fact["kind"], fact["key"]), target)
        for fact in node.metadata.get("boundary_defines", ()):
            add((fact["kind"], fact["key"]), node)
    return served


def resolve_boundaries(graph: RustworkxCodeGraph, index: Optional[_Index] = None) -> int:
    """Rebuild all boundary edges from node metadata. Returns the number of edges."""
    with graph._lock:
        graph.remove_relationships_by_type(RelationshipType.DEPENDS_ON)
        index = index or _Index(graph)
        providers = _providers(graph, index)
        if not providers:
            return 0
        added = 0
        for user in list(graph.nodes.values()):
            if user.node_type != NodeType.FUNCTION:
                continue
            linked: Set[str] = set()
            for fact in user.metadata.get("boundary_uses", ()):
                key = (fact["kind"], fact["key"])
                found = providers.get(key, [])
                if len(found) != 1:
                    continue  # no provider, or an ambiguous one
                target = found[0]
                if target.id == user.id or target.id in linked:
                    continue
                linked.add(target.id)
                rel = UniversalRelationship(
                    id=f"boundary:{user.id}->{target.id}",
                    source_id=user.id,
                    target_id=target.id,
                    relationship_type=RelationshipType.DEPENDS_ON,
                    metadata={
                        "resolution": "boundary", "boundary": fact["kind"], "key": fact["key"],
                    },
                    strength=BOUNDARY_STRENGTH,
                )
                if graph.add_relationship(rel) is not None:
                    added += 1
    logger.info("Resolved %d boundary edges", added)
    return added

