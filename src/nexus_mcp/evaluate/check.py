"""Post-change check: what does a diff touch that it did not update?

`nexus-mcp-ci check` reads the git diff of a branch, maps each changed line to the symbol
that holds it, and asks the stored call graph who depends on those symbols. A caller that
the diff did not touch is a finding. The check runs outside the model, so it adds no
tokens to a session, and it needs no embedding model: only the stored graph, git, and
the parser.

Two graphs make the answer honest. The *before* graph is the stored graph with the
changed files read at the base commit. The *after* graph is the stored graph with the
changed files read from the working tree. Callers come from *before*: a symbol that the
change removed or renamed has callers only there.

The result is a lower bound, like every call-graph answer in this project. Edges are
static and name-based. A dynamic call, a string reference and a call through a callback
are not seen. `--fail-on` decides which findings fail a build.
"""

import json
import logging
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from nexus_mcp.core.graph_models import (
    NodeType,
    RelationshipType,
    UniversalGraph,
    UniversalNode,
    is_module_body,
)
from nexus_mcp.core.keys import natural_keys, relative_path
from nexus_mcp.engines.fusion import is_test_path
from nexus_mcp.indexing.boundary_resolver import edge_function_name
from nexus_mcp.parsing.boundaries import KIND_FUNCTION

logger = logging.getLogger(__name__)

SEVERITIES = ("low", "medium", "high")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES, start=1)}
MAX_STALE_FILES = 200
MAX_REFERENCE_SYMBOLS = 20
MAX_TRANSITIVE_PER_SYMBOL = 20
MAX_AFFECTED_TESTS = 40
MAX_BOUNDARY_USERS_FOR_TESTS = 50
MIN_REFERENCE_NAME = 4
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_GIT_TIMEOUT_S = 60

Runner = Callable[..., Any]


class CheckError(Exception):
    """A failure with its exit code: 2 for a usage or git error, 3 for the index."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Hunk:
    """One `@@` block of `git diff --unified=0`. A length of 0 means a pure insertion or
    deletion on that side."""

    old_start: int
    old_len: int
    new_start: int
    new_len: int

    def old_lines(self) -> range:
        return range(self.old_start, self.old_start + self.old_len)

    def new_lines(self) -> range:
        return range(self.new_start, self.new_start + self.new_len)


@dataclass
class FileDiff:
    """The change of one file. `status` is A (added), M (modified) or D (deleted)."""

    path: str
    status: str
    hunks: List[Hunk] = field(default_factory=list)


@dataclass
class ChangedSymbol:
    key: str
    name: str
    file: str
    kind: str
    status: str  # modified, added or removed
    line: int
    signature_changed: bool = False


@dataclass
class Finding:
    severity: str
    # untouched_caller, caller_lines_unchanged, transitive_caller, reference_untouched,
    # boundary_untouched, boundary_lines_unchanged
    kind: str
    symbol: str  # the key of the changed symbol
    name: str
    caller: str  # the key of the caller, or the file for a reference
    file: str
    line: int
    reason: str


@dataclass
class CheckReport:
    base: str
    files: List[str]
    changed_symbols: List[ChangedSymbol]
    findings: List[Finding]
    affected_tests: List[str]
    file_level_changes: List[str]
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --- git ---------------------------------------------------------------------------


def _run_git(project: Path, args: Sequence[str], run: Runner) -> Any:
    env = {**os.environ, "LC_ALL": "C", "GIT_PAGER": "cat"}
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)  # a git hook that starts us must not aim git at another repo
    try:
        return run(
            ["git", "-C", str(project), *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S, env=env,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise CheckError(f"git failed: {e}") from e


def _git(project: Path, *args: str, run: Runner = subprocess.run, check: bool = True) -> str:
    """Run git in `project` and return stdout. `run` is injectable for tests.

    With check=False a failed command gives "" instead of an error.
    """
    done = _run_git(project, args, run)
    if done.returncode != 0:
        if check:
            raise CheckError(f"git {' '.join(args[:2])} failed: {done.stderr.strip()[:300]}")
        return ""
    return done.stdout


def git_base(project: Path, base: Optional[str] = None, run: Runner = subprocess.run) -> str:
    """The commit to compare with: `base`, else the merge base with the default branch.

    On the default branch itself the merge base is HEAD, so only uncommitted work counts.
    """
    if base:
        return _git(project, "rev-parse", "--verify", f"{base}^{{commit}}", run=run).strip()
    for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        found = _git(project, "merge-base", "HEAD", ref, run=run, check=False).strip()
        if found:
            return found
    return _git(project, "rev-parse", "--verify", "HEAD", run=run).strip()


def parse_unified_diff(text: str) -> List[FileDiff]:
    """Parse `git diff --unified=0 --no-renames` into one FileDiff for each file."""
    diffs: List[FileDiff] = []
    current: Optional[FileDiff] = None
    old_path = new_path = ""
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current, old_path, new_path = None, "", ""
        elif line.startswith("--- "):
            old_path = line[4:].strip()
        elif line.startswith("+++ "):
            new_path = line[4:].strip()
            if new_path == "/dev/null":
                path, status = old_path[2:], "D"
            elif old_path == "/dev/null":
                path, status = new_path[2:], "A"
            else:
                path, status = new_path[2:], "M"
            current = FileDiff(path=path, status=status)
            diffs.append(current)
        elif line.startswith("@@") and current is not None:
            found = _HUNK.match(line)
            if found:
                old_start, old_len, new_start, new_len = found.groups()
                current.hunks.append(Hunk(
                    int(old_start), 1 if old_len is None else int(old_len),
                    int(new_start), 1 if new_len is None else int(new_len),
                ))
    return diffs


def git_diff_files(
    project: Path, base: str, staged: bool = False, run: Runner = subprocess.run
) -> List[FileDiff]:
    """The files that differ from `base`, with their hunks. Untracked files count as added."""
    args = ["diff", "--unified=0", "--no-color", "--no-renames"]
    args += ["--cached", base] if staged else [base]
    diffs = parse_unified_diff(_git(project, *args, run=run))
    if not staged:
        untracked = _git(project, "ls-files", "--others", "--exclude-standard", run=run)
        known = {d.path for d in diffs}
        diffs += [
            FileDiff(path=p, status="A") for p in untracked.splitlines() if p and p not in known
        ]
    return diffs


def git_show(project: Path, base: str, path: str, run: Runner = subprocess.run) -> Optional[str]:
    """The text of `path` at `base`, or None when it did not exist there."""
    done = _run_git(project, ("show", f"{base}:{path}"), run)
    return done.stdout if done.returncode == 0 else None


# --- symbols -----------------------------------------------------------------------


def _file_nodes(graph: Any, abs_path: str) -> List[UniversalNode]:
    """The function and class nodes of one file."""
    ids = graph._file_nodes.get(abs_path, set())
    return [
        graph.nodes[i] for i in ids
        if i in graph.nodes and graph.nodes[i].node_type in (NodeType.FUNCTION, NodeType.CLASS)
    ]


def innermost(nodes: Sequence[UniversalNode], line: int) -> Optional[UniversalNode]:
    """The smallest node that holds `line`. The `<top-level>` node counts only when no
    other node does, because it spans the whole file."""
    holders = [
        n for n in nodes
        if n.metadata.get("decorated_from", n.location.start_line) <= line <= n.location.end_line
    ]
    real = [n for n in holders if not is_module_body(n)]
    pool = real or holders
    if not pool:
        return None
    return min(pool, key=lambda n: (n.location.end_line - n.location.start_line, n.id))


def signature_of(text: str) -> Tuple[str, str]:
    """(parameters, return annotation) of the function that starts `text`, whitespace
    collapsed. Both are empty for text with no parameter list (a class, a bare arrow).

    Works for Python and JS/TS alike: the parameters are what sits in the first pair of
    parentheses, the annotation is what follows the closing one, up to the body.
    """
    start = text.find("(")
    if start < 0:
        return "", ""
    depth, end, quote = 0, -1, ""
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if ch == quote and text[i - 1] != "\\":
                quote = ""
            continue
        if ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return "", ""
    params = " ".join(text[start + 1:end].split())
    tail = text[end + 1:]
    annotation = ""
    arrow = re.match(r"\s*->\s*([^:]+?)\s*:", tail)  # Python
    if arrow:
        annotation = arrow.group(1)
    else:
        colon = re.match(r"\s*:\s*([^{=]+?)\s*(?:\{|=>)", tail)  # TypeScript
        if colon:
            annotation = colon.group(1)
    return params, " ".join(annotation.split())


def _node_text(lines: Sequence[str], node: UniversalNode, limit: int = 40) -> str:
    start = node.location.start_line - 1
    end = min(node.location.end_line, start + limit)
    return "\n".join(lines[start:end])


# --- the two views -----------------------------------------------------------------


@dataclass
class Views:
    root: Path
    before: Any
    after: Any
    before_text: Dict[str, List[str]]
    after_text: Dict[str, List[str]]
    refreshed: List[str]


def _load_graph(graph_db: Path) -> Any:
    from nexus_mcp.persistence.store import GraphPersistence

    try:
        graph = GraphPersistence(str(graph_db)).load()
    except Exception as e:  # noqa: BLE001 - a bad file is "no usable index"
        raise CheckError(f"Cannot read the stored graph {graph_db}: {e}", 3) from e
    if graph is None or not graph.nodes:
        raise CheckError("The stored graph is empty. Run `nexus-mcp-ci index` first.", 3)
    return graph


def detect_root(graph: Any, project: Path) -> Path:
    """The folder that the stored node paths start with: the project, or its real path."""
    modules = [
        n.location.file_path for n in graph.nodes.values() if n.node_type == NodeType.MODULE
    ]
    for candidate in (project.resolve(), project.absolute(), Path(os.path.realpath(project))):
        prefix = str(candidate).rstrip("/") + "/"
        if any(p.replace("\\", "/").startswith(prefix) for p in modules):
            return candidate
    raise CheckError(
        "The stored index does not cover this folder. Run `nexus-mcp-ci index` in it first.", 3
    )


def stale_files(graph: Any, graph_db: Path, root: Path, skip: Set[str]) -> List[str]:
    """Indexed files that changed on disk after the graph was saved, outside the diff."""
    try:
        saved = graph_db.stat().st_mtime
    except OSError:
        return []
    stale: List[str] = []
    for node in graph.nodes.values():
        if node.node_type != NodeType.MODULE:
            continue
        path = node.location.file_path
        if path in skip:
            continue
        try:
            if os.stat(path).st_mtime > saved + 1:
                stale.append(path)
        except OSError:
            stale.append(path)  # deleted since the index
    return stale


def build_views(
    project: Path, store: Path, base: str, diffs: List[FileDiff],
    run: Runner = subprocess.run,
) -> Views:
    """Load the stored graph twice and swap the changed files: old text in `before`,
    working tree in `after`."""
    from nexus_mcp.indexing.call_resolver import resolve_calls
    from nexus_mcp.indexing.pipeline import _transfer_graph
    from nexus_mcp.parsing.astgrep_parser import AstGrepParser

    graph_db = store / "graph.db"
    if not graph_db.is_file():
        raise CheckError(
            "No index found. Run `nexus-mcp-ci index` (or use the MCP server) first.", 3
        )
    before, after = _load_graph(graph_db), _load_graph(graph_db)
    root = detect_root(after, project)

    def absolute(rel: str) -> str:
        return str(root / rel)

    diff_abs = {absolute(d.path) for d in diffs}
    refreshed = stale_files(after, graph_db, root, diff_abs)
    if len(refreshed) > MAX_STALE_FILES:
        raise CheckError(
            f"{len(refreshed)} indexed files changed after the last index. "
            "Run `nexus-mcp-ci index` first.", 3,
        )

    parser = AstGrepParser()
    before_text: Dict[str, List[str]] = {}
    after_text: Dict[str, List[str]] = {}
    before_new, after_new = UniversalGraph(), UniversalGraph()

    def read_working(abs_path: str) -> Optional[str]:
        try:
            return Path(abs_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    for diff in diffs:
        abs_path = absolute(diff.path)
        for graph in (before, after):
            graph.remove_file_nodes(abs_path)
        if not parser.can_parse(abs_path):
            continue
        if diff.status != "A":
            old = git_show(project, base, diff.path, run=run)
            if old is not None:
                before_text[abs_path] = old.splitlines()
                parser.parse_source(old, abs_path, before_new)
        if diff.status != "D":
            new = read_working(abs_path)
            if new is not None:
                after_text[abs_path] = new.splitlines()
                parser.parse_source(new, abs_path, after_new)
    for abs_path in refreshed:
        for graph in (before, after):
            graph.remove_file_nodes(abs_path)
        text = read_working(abs_path)
        if text is not None and parser.can_parse(abs_path):
            after_text[abs_path] = text.splitlines()
            parser.parse_source(text, abs_path, before_new)
            parser.parse_source(text, abs_path, after_new)

    _transfer_graph(before_new, before)
    _transfer_graph(after_new, after)
    resolve_calls(before)
    resolve_calls(after)
    return Views(root, before, after, before_text, after_text, refreshed)


# --- the check ---------------------------------------------------------------------


def _text_lines(views: Views, graph_side: str, abs_path: str) -> List[str]:
    source = views.before_text if graph_side == "before" else views.after_text
    if abs_path in source:
        return source[abs_path]
    try:  # an unchanged file: the working tree is also its old text
        return Path(abs_path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def changed_symbols(views: Views, diffs: List[FileDiff]) -> Tuple[List[ChangedSymbol], List[str]]:
    """The symbols that the diff touches, and the files with a change outside any symbol."""
    root = str(views.root)
    out: Dict[str, ChangedSymbol] = {}
    file_level: List[str] = []
    for diff in diffs:
        abs_path = str(views.root / diff.path)
        rel = relative_path(abs_path, root)
        before_nodes = _file_nodes(views.before, abs_path)
        after_nodes = _file_nodes(views.after, abs_path)
        before_keys = natural_keys(before_nodes, root)
        after_keys = natural_keys(after_nodes, root)
        before_by_key = {before_keys[n.id]: n for n in before_nodes}
        after_by_key = {after_keys[n.id]: n for n in after_nodes}

        touched_before: Set[str] = set()
        touched_after: Set[str] = set()
        if diff.status == "A":
            touched_after = {k for k, n in after_by_key.items() if not is_module_body(n)}
        elif diff.status == "D":
            touched_before = {k for k, n in before_by_key.items() if not is_module_body(n)}
        else:
            outside = 0
            for hunk in diff.hunks:
                for line in hunk.old_lines():
                    node = innermost(before_nodes, line)
                    if node is None:
                        outside += 1
                    else:
                        touched_before.add(before_keys[node.id])
                for line in hunk.new_lines():
                    node = innermost(after_nodes, line)
                    if node is None:
                        outside += 1
                    else:
                        touched_after.add(after_keys[node.id])
            if outside:
                file_level.append(rel)
        for key in touched_before | touched_after:
            node = after_by_key.get(key) or before_by_key[key]
            if key in before_by_key and key in after_by_key:
                status = "modified"
            elif key in before_by_key:
                status = "removed"
            else:
                status = "added"
            sig = False
            if (
                status == "modified" and node.node_type == NodeType.FUNCTION
                and not is_module_body(node)
            ):
                old_text = _node_text(
                    _text_lines(views, "before", abs_path), before_by_key[key]
                )
                new_text = _node_text(_text_lines(views, "after", abs_path), after_by_key[key])
                sig = signature_of(old_text) != signature_of(new_text)
            out[key] = ChangedSymbol(
                key=key, name=node.name, file=rel, kind=node.node_type.value, status=status,
                line=node.location.start_line, signature_changed=sig,
            )
    ordered = sorted(out.values(), key=lambda s: (s.file, s.line, s.key))
    return ordered, sorted(set(file_level))


def _is_module_body_key(key: str) -> bool:
    return key.endswith("::<top-level>")


def analyze(
    views: Views, diffs: List[FileDiff], symbols: List[ChangedSymbol], depth: int = 3,
    max_findings: int = 50,
) -> Tuple[List[Finding], List[str], Dict[str, Any]]:
    """Findings, affected test files and counters for the changed symbols."""
    root = str(views.root)
    diff_files = {d.path for d in diffs}
    changed_keys = {s.key for s in symbols}
    by_key_before: Dict[str, UniversalNode] = {}
    keys_cache: Dict[Tuple[str, str], Dict[str, str]] = {}

    def keys_of(graph: Any, side: str, abs_path: str) -> Dict[str, str]:
        cache_key = (side, abs_path)
        if cache_key not in keys_cache:
            keys_cache[cache_key] = natural_keys(_file_nodes(graph, abs_path), root)
        return keys_cache[cache_key]

    def key_of(graph: Any, side: str, node: UniversalNode) -> str:
        return keys_of(graph, side, node.location.file_path).get(node.id, "")

    for symbol in symbols:
        if symbol.status == "added":
            continue
        abs_path = str(views.root / symbol.file)
        for node in _file_nodes(views.before, abs_path):
            if keys_of(views.before, "before", abs_path).get(node.id) == symbol.key:
                by_key_before[symbol.key] = node

    findings: List[Finding] = []
    seen: Set[Tuple[str, str, str]] = set()
    test_files: Set[str] = set()
    impacted_total = 0
    edge_types = {RelationshipType.CALLS, RelationshipType.USES, RelationshipType.DEPENDS_ON}

    def add(finding: Finding) -> None:
        marker = (finding.kind, finding.symbol, finding.caller)
        if marker not in seen:
            seen.add(marker)
            findings.append(finding)

    for symbol in symbols:
        node = by_key_before.get(symbol.key)
        if node is None or _is_module_body_key(symbol.key):
            continue
        risk = "high" if symbol.status == "removed" or symbol.signature_changed else "medium"
        why = (
            "was removed" if symbol.status == "removed"
            else "changed its parameters or return type" if symbol.signature_changed
            else "changed"
        )
        direct = views.before.get_callers(node.id)
        direct_ids = {c.id for c in direct}
        for caller in direct:
            caller_key = key_of(views.before, "before", caller)
            if caller_key == symbol.key or caller_key in changed_keys:
                continue  # the caller was changed in this diff too
            rel = relative_path(caller.location.file_path, root)
            kind = "caller_lines_unchanged" if rel in diff_files else "untouched_caller"
            where = (
                "its file is in the diff, but this function is not"
                if kind == "caller_lines_unchanged" else "its file is not in the diff"
            )
            add(Finding(
                severity=risk, kind=kind, symbol=symbol.key, name=symbol.name,
                caller=caller_key, file=rel, line=caller.location.start_line,
                reason=f"calls `{symbol.name}`, which {why}; {where}",
            ))
        transitive = views.before.get_transitive_callers(
            node.id, max_depth=max(depth, 1), edge_types=edge_types
        )
        impacted_total += len(transitive)
        listed = 0
        for caller in transitive:
            rel = relative_path(caller.location.file_path, root)
            if is_test_path(rel):
                test_files.add(rel)
            caller_key = key_of(views.before, "before", caller)
            if (
                risk != "high" or caller.id in direct_ids or caller_key in changed_keys
                or rel in diff_files or listed >= MAX_TRANSITIVE_PER_SYMBOL
            ):
                continue
            listed += 1
            add(Finding(
                severity="low", kind="transitive_caller", symbol=symbol.key,
                name=symbol.name, caller=caller_key, file=rel, line=caller.location.start_line,
                reason=f"depends on `{symbol.name}`, which {why}, through a chain of calls",
            ))

    _add_boundary_findings(
        views, symbols, by_key_before, diff_files, key_of, test_files, add, depth, edge_types
    )
    _add_reference_findings(views, symbols, diff_files, seen, test_files, add)

    findings.sort(key=lambda f: (-SEVERITY_RANK[f.severity], f.file, f.line, f.kind))
    stats = {
        "changed_files": len(diffs),
        "changed_symbols": len(symbols),
        "findings": len(findings),
        "impacted_symbols": impacted_total,
        "refreshed_stale_files": len(views.refreshed),
        "truncated": len(findings) > max_findings,
    }
    return findings[:max_findings], sorted(test_files)[:MAX_AFFECTED_TESTS], stats


def _provided_keys(node: UniversalNode) -> Set[Tuple[str, str]]:
    """The (kind, key) pairs that a node serves, defines or alters (ADR-026)."""
    keys: Set[Tuple[str, str]] = set()
    for field_name in ("boundary_serves", "boundary_defines", "boundary_touches"):
        for fact in node.metadata.get(field_name, ()):
            keys.add((fact["kind"], fact["key"]))
    if node.node_type == NodeType.FUNCTION and (
        is_module_body(node) or node.metadata.get("export") == "default"
    ):
        name = edge_function_name(node.location.file_path)
        if name:
            keys.add((KIND_FUNCTION, name))
    return keys


def _boundary_consumers(graph: Any) -> Tuple[
    Dict[Tuple[str, str], List[UniversalNode]], Dict[str, List[Tuple[UniversalNode, str, str]]]
]:
    """Who uses each key, and who reaches each node through a boundary edge (before graph)."""
    by_key: Dict[Tuple[str, str], List[UniversalNode]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.node_type != NodeType.FUNCTION:
            continue
        for fact in node.metadata.get("boundary_uses", ()):
            by_key[(fact["kind"], fact["key"])].append(node)
    by_target: Dict[str, List[Tuple[UniversalNode, str, str]]] = defaultdict(list)
    for rel in graph.get_relationships_by_type(RelationshipType.DEPENDS_ON):
        source = graph.nodes.get(rel.source_id)
        if source is not None:
            by_target[rel.target_id].append(
                (source, rel.metadata.get("boundary", ""), rel.metadata.get("key", ""))
            )
    return by_key, by_target


def _add_boundary_findings(
    views: Views, symbols: List[ChangedSymbol], by_key_before: Dict[str, UniversalNode],
    diff_files: Set[str], key_of: Callable[[Any, str, UniversalNode], str],
    test_files: Set[str], add_to: Callable[[Finding], None], depth: int,
    edge_types: Set[RelationshipType],
) -> None:
    """Users of a route, an edge function or a table whose provider the diff changed.

    A boundary has no call edge in the code, so the findings come from the boundary
    facts (ADR-026). A change is `high` when the provider is gone, serves other keys, drops
    columns, or drops or renames part of a table. Otherwise it is `medium`.
    """
    root = str(views.root)
    changed_keys = {s.key for s in symbols}
    by_key, by_target = _boundary_consumers(views.before)
    listed_users = 0
    for symbol in symbols:
        abs_path = str(views.root / symbol.file)
        before = by_key_before.get(symbol.key)
        after = None
        if symbol.status != "removed":
            keys = natural_keys(_file_nodes(views.after, abs_path), root)
            after = next((n for n in _file_nodes(views.after, abs_path)
                          if keys.get(n.id) == symbol.key), None)
        node = before or after
        if node is None:
            continue
        touches = (after or node).metadata.get("boundary_touches", ())
        if symbol.status == "added" and not touches:
            continue  # a new provider has no user yet
        before_keys = _provided_keys(before) if before else set()
        after_keys = _provided_keys(after) if after else set()
        lost = before_keys - after_keys
        destructive = any(t.get("destructive") for t in touches)
        columns_lost = (
            sorted(set(before.metadata.get("columns", ())) - set(after.metadata.get("columns", ())))
            if before and after else []
        )
        risk = (
            "high" if symbol.status == "removed" or symbol.signature_changed or lost
            or destructive or columns_lost else "medium"
        )
        why = (
            "was removed" if symbol.status == "removed"
            else "no longer serves it" if lost
            else f"lost the columns {', '.join(columns_lost)}" if columns_lost
            else "is dropped or renamed" if destructive
            else "changed its parameters or return type" if symbol.signature_changed
            else "changed"
        )
        found: Dict[str, Tuple[UniversalNode, str, str]] = {}
        if before is not None:
            for user, kind, key in by_target.get(before.id, ()):
                found.setdefault(user.id, (user, kind, key))
        for kind, key in before_keys | _provided_keys(node):
            for user in by_key.get((kind, key), ()):
                found.setdefault(user.id, (user, kind, key))
        for user, kind, key in found.values():
            user_key = key_of(views.before, "before", user)
            if user_key == symbol.key or user_key in changed_keys:
                continue
            rel = relative_path(user.location.file_path, root)
            same_file_changed = rel in diff_files
            if is_test_path(rel):
                test_files.add(rel)
            if listed_users < MAX_BOUNDARY_USERS_FOR_TESTS:
                listed_users += 1
                for caller in views.before.get_transitive_callers(
                    user.id, max_depth=max(depth, 1), edge_types=edge_types
                ):
                    caller_rel = relative_path(caller.location.file_path, root)
                    if is_test_path(caller_rel):
                        test_files.add(caller_rel)
            add_to(Finding(
                severity=risk,
                kind="boundary_lines_unchanged" if same_file_changed else "boundary_untouched",
                symbol=symbol.key, name=symbol.name, caller=user_key, file=rel,
                line=user.location.start_line,
                reason=(
                    f"uses the {kind} `{key}`, which `{symbol.name}` serves and {why}; "
                    + ("its file is in the diff, but this function is not"
                       if same_file_changed else "its file is not in the diff")
                ),
            ))


def _add_reference_findings(
    views: Views, symbols: List[ChangedSymbol], diff_files: Set[str],
    seen: Set[Tuple[str, str, str]], test_files: Set[str],
    add_to: Callable[[Finding], None],
) -> None:
    """Whole-word uses of a removed or re-signed name in files that the diff did not touch.

    A name that several definitions share gets no call edge, and a string or a callback
    gives none either. Text search finds those uses. Only names that are long enough to
    mean something are searched, and only for the symbols that carry the most risk.
    """
    from nexus_mcp.indexing.call_resolver import COMMON_METHOD_NAMES

    risky = [
        s for s in symbols
        if (s.status == "removed" or s.signature_changed)
        and len(s.name) >= MIN_REFERENCE_NAME and s.name not in COMMON_METHOD_NAMES
        and not _is_module_body_key(s.key)
    ][:MAX_REFERENCE_SYMBOLS]
    if not risky:
        return None
    try:
        from nexus_mcp.engines.live_grep import LiveGrepEngine

        engine = LiveGrepEngine(str(views.root))
    except Exception as e:  # noqa: BLE001 - references are a bonus, never a failure
        logger.debug("Reference search unavailable: %s", e)
        return None
    for symbol in risky:
        try:
            found = engine.references(symbol.name, max_lines=200, max_lines_per_file=3)
        except Exception as e:  # noqa: BLE001
            logger.debug("Reference search failed for %s: %s", symbol.name, e)
            continue
        if not found:
            continue
        paths: Dict[str, str] = {}
        for bucket in ("files", "more_files", "test_files"):
            for path, hit in (found.get(bucket) or {}).items():
                first = hit[0].split(":", 1)[0] if isinstance(hit, list) and hit else "1"
                clean = path.replace("\\", "/")
                paths[clean[2:] if clean.startswith("./") else clean] = first
        for path, first in sorted(paths.items()):
            if is_test_path(path):
                test_files.add(path)
            if path in diff_files or path == symbol.file:
                continue
            if any(m[1] == symbol.key and m[2].startswith(path + "::") for m in seen):
                continue  # a call edge already named a function of this file
            add_to(Finding(
                severity="medium" if symbol.status == "removed" else "low",
                kind="reference_untouched", symbol=symbol.key, name=symbol.name,
                caller=path, file=path, line=int(first) if str(first).isdigit() else 1,
                reason=f"names `{symbol.name}`, which "
                f"{'was removed' if symbol.status == 'removed' else 'changed its signature'};"
                " found by text search, no call edge",
            ))
    return None


def run_check(
    project: Path, store: Path, base: Optional[str] = None, staged: bool = False,
    depth: int = 3, max_findings: int = 50, run: Runner = subprocess.run,
) -> CheckReport:
    """The whole check for one project. Raises CheckError for a usage or index problem."""
    project = Path(project)
    if not project.is_dir():
        raise CheckError(f"Not a folder: {project}")
    base_sha = "HEAD" if staged else git_base(project, base, run=run)
    diffs = git_diff_files(project, base_sha, staged=staged, run=run)
    store_rel = relative_path(str(Path(store).resolve()), str(project.resolve()))
    if not store_rel.startswith("/"):  # the index folder is inside the project
        diffs = [d for d in diffs if d.path != store_rel and not d.path.startswith(store_rel + "/")]
    supported = [d for d in diffs if _supported(d.path)]
    views = build_views(project, store, base_sha, supported, run=run)
    symbols, file_level = changed_symbols(views, supported)
    findings, tests, stats = analyze(
        views, supported, symbols, depth=depth, max_findings=max_findings
    )
    stats["unsupported_files"] = len(diffs) - len(supported)
    return CheckReport(
        base=base_sha, files=sorted(d.path for d in diffs), changed_symbols=symbols,
        findings=findings, affected_tests=tests, file_level_changes=file_level, stats=stats,
    )


def _supported(path: str) -> bool:
    from nexus_mcp.parsing.language_registry import get_supported_extensions

    return Path(path).suffix.lower() in get_supported_extensions()


# --- output ------------------------------------------------------------------------


def render_markdown(report: CheckReport) -> str:
    """A report that pastes into a pull-request comment."""
    counts = defaultdict(int)
    for f in report.findings:
        counts[f.severity] += 1
    head = ", ".join(f"{counts[s]} {s}" for s in reversed(SEVERITIES) if counts[s]) or "none"
    lines = [
        "# nexus-mcp-ci check",
        "",
        f"Base `{report.base[:12]}` · {report.stats['changed_files']} changed files · "
        f"{report.stats['changed_symbols']} changed symbols · findings: {head}",
        "",
    ]
    if report.findings:
        lines += [
            "## Not updated", "",
            "| Severity | Changed symbol | Not updated | Why |", "|---|---|---|---|",
        ]
        for f in report.findings:
            where = f"`{f.caller}`" if f.kind != "reference_untouched" else f"`{f.file}:{f.line}`"
            lines.append(f"| {f.severity} | `{f.symbol}` | {where} | {f.reason} |")
        if report.stats.get("truncated"):
            lines.append("")
            lines.append("More findings exist. Raise `--max-findings` to see them.")
        lines.append("")
    else:
        lines += ["No caller or reference was left behind by this diff.", ""]
    if report.affected_tests:
        lines += ["## Tests to run", ""] + [f"- `{t}`" for t in report.affected_tests] + [""]
    if report.file_level_changes:
        lines += [
            "## Changes outside any function",
            "",
            "These files changed in lines that no function or class holds: "
            + ", ".join(f"`{f}`" for f in report.file_level_changes) + ".",
            "",
        ]
    if report.changed_symbols:
        lines += ["## Changed symbols", "", "| Symbol | Change | Signature |", "|---|---|---|"]
        for s in report.changed_symbols:
            lines.append(
                f"| `{s.key}` | {s.status} | {'changed' if s.signature_changed else '-'} |"
            )
        lines.append("")
    lines.append(
        "_A lower bound: edges are static and name-based. Dynamic calls, callbacks and "
        "string references are not seen._"
    )
    return "\n".join(lines) + "\n"


def render_json(report: CheckReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"


def exit_code(report: CheckReport, fail_on: str) -> int:
    """1 when a finding reaches the `fail_on` severity, else 0. `never` always gives 0."""
    if fail_on == "never":
        return 0
    threshold = SEVERITY_RANK[fail_on]
    return 1 if any(SEVERITY_RANK[f.severity] >= threshold for f in report.findings) else 0
