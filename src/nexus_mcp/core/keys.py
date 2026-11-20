"""Stable keys for graph symbols.

A graph node id is random on each index run (`func:<uuid>-<counter>`), so it cannot name
a symbol from last week. The natural key can:

    <file>::<type>::<qualified name>

`file` is relative to the project root with `/` separators. `qualified name` is
`Class.method` for a method and the bare name for anything else. When two nodes of one
file share a key (an overload set, a redefinition), `#1`, `#2` follow in the order of
their first line. The ordinal changes only when a symbol is added or removed above it.

A rename and a move between files are new keys. That is on purpose: both are changes
that a reader should look at.

No import of the graph engine or of LanceDB, so a CLI can use this module alone.
"""

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, Optional

from nexus_mcp.core.graph_models import UniversalNode

KEY_SEPARATOR = "::"


def relative_path(file_path: str, root: Optional[str] = None) -> str:
    """`file_path` relative to `root` when it lies under it, with `/` separators."""
    path = str(file_path).replace("\\", "/")
    if root:
        try:
            return Path(path).relative_to(Path(str(root).replace("\\", "/"))).as_posix()
        except ValueError:
            pass
    return path


def qualified_name(node: UniversalNode) -> str:
    """`Class.method` for a method (a node with a parent class), else the name."""
    parent = node.metadata.get("parent_class")
    return f"{parent}.{node.name}" if parent else node.name


def natural_key(node: UniversalNode, root: Optional[str] = None) -> str:
    """The key of one node, without an ordinal. See natural_keys() for a whole file."""
    return KEY_SEPARATOR.join((
        relative_path(node.location.file_path, root),
        node.node_type.value,
        qualified_name(node),
    ))


def natural_keys(
    nodes: Iterable[UniversalNode], root: Optional[str] = None
) -> Dict[str, str]:
    """Node id to key for many nodes, with `#n` ordinals where a key repeats.

    Pass every node of a file (the nodes of other files never collide, because the file
    is part of the key). The ordinal follows the first line, then the last line, then
    the id, so it is the same on every run for the same file.
    """
    groups: Dict[str, list] = defaultdict(list)
    for node in nodes:
        groups[natural_key(node, root)].append(node)
    keys: Dict[str, str] = {}
    for key, group in groups.items():
        if len(group) == 1:
            keys[group[0].id] = key
            continue
        group.sort(key=lambda n: (n.location.start_line, n.location.end_line, n.id))
        for ordinal, node in enumerate(group, start=1):
            keys[node.id] = f"{key}#{ordinal}"
    return keys


def fingerprint(text: str) -> str:
    """`sha256:<hex>` of a symbol's source, with trailing whitespace stripped.

    A decision or a check can store it, and later ask whether the symbol changed.
    """
    stripped = "\n".join(line.rstrip() for line in text.rstrip().splitlines())
    return "sha256:" + hashlib.sha256(stripped.encode("utf-8")).hexdigest()
