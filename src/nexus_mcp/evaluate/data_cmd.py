"""``nexus-mcp-ci data``: show, export and delete what Nexus-MCP stores."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from nexus_mcp.evaluate import paths

TARGETS = ("index", "memories", "graph", "evals", "clones", "models")
MEMORIES_TABLE = "memories.lance"
CHUNKS_TABLE = "chunks.lance"


def looks_like_store(store: Path) -> bool:
    """True when ``store`` has the marks of a nexus store. Guards ``clear`` against a wrong path."""
    return (store / "lancedb").is_dir() or (store / "graph.db").is_file()


def git_ignored(project: Path, store: Path) -> Optional[bool]:
    """True/False when ``project`` is a git repo and ``store`` is/is not ignored. None otherwise."""
    try:
        inside = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if inside.returncode != 0:
            return None
        rel = store.resolve().relative_to(Path(project).resolve())
        ignored = subprocess.run(
            ["git", "-C", str(project), "check-ignore", "-q", str(rel)],
            capture_output=True,
            timeout=10,
        )
        return ignored.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def locations(project: Path) -> List[Dict[str, Any]]:
    """Every place Nexus-MCP can keep data for ``project``, with size and whether it holds code."""
    store = paths.project_store(project)
    rows = [
        (
            "index",
            "Code index (chunks, vectors, keyword index)",
            store / "lancedb" / CHUNKS_TABLE,
            True,
        ),
        ("memories", "Memories", store / "lancedb" / MEMORIES_TABLE, False),
        ("graph", "Code graph", store / "graph.db", False),
        ("evals", "Saved eval results", paths.eval_home(), False),
        ("clones", "Cloned repos for eval", paths.eval_cache(), True),
        ("models", "Embedding model (shared by all projects)", paths.model_dir(), False),
    ]
    out = []
    for key, label, path, holds_code in rows:
        size, files = paths.dir_size(path)
        out.append(
            {
                "target": key,
                "label": label,
                "path": str(path),
                "exists": Path(path).exists(),
                "bytes": size,
                "files": files,
                "holds_code": holds_code,
            }
        )
    return out


def where(project: Path) -> str:
    """Text for ``data where``."""
    project = Path(project).resolve()
    store = paths.project_store(project)
    lines = [f"Nexus-MCP data for {project}", ""]
    for row in locations(project):
        if row["exists"]:
            state = f"{paths.human_size(row['bytes'])}, {row['files']} files"
        else:
            state = "not present"
        code = "  (holds code)" if row["holds_code"] else ""
        lines.append(f"  {row['target']:<9} {row['path']}\n            {state}{code}")
    ignored = git_ignored(project, store)
    lines.append("")
    if ignored is False and store.exists():
        lines.append(f"WARNING: {store} is not ignored by git. The index holds code snippets.")
        lines.append(f"  Local fix (not committed): echo '{store.name}/' >> .git/info/exclude")
    elif ignored:
        lines.append(f"{store.name}/ is ignored by git.")
    lines.append("Nothing leaves this machine unless you run `eval --agent` or `eval share`.")
    lines.append("Remove data: nexus-mcp-ci data clear --help")
    return "\n".join(lines)


def _paths_for(project: Path, target: str) -> List[Path]:
    store = paths.project_store(project)
    return {
        "index": [store / "lancedb" / CHUNKS_TABLE, store / "index_metadata.json"],
        "memories": [store / "lancedb" / MEMORIES_TABLE],
        "graph": [store / "graph.db"],
        "evals": [paths.eval_home()],
        "clones": [paths.eval_cache() / "repos"],
        "models": [paths.model_dir()],
    }[target]


def plan_clear(project: Path, targets: List[str]) -> List[Dict[str, Any]]:
    """What ``clear`` would delete. Raises ValueError for a path that is not a nexus store."""
    project = Path(project).resolve()
    store = paths.project_store(project)
    if {"index", "memories", "graph"} & set(targets) and not looks_like_store(store):
        raise ValueError(
            f"{store} does not look like a Nexus-MCP store (no lancedb/ and no graph.db). "
            "Nothing deleted."
        )
    plan = []
    for target in targets:
        for path in _paths_for(project, target):
            if path.exists():
                size, files = paths.dir_size(path)
                plan.append({"target": target, "path": path, "bytes": size, "files": files})
    return plan


def clear(project: Path, targets: List[str], yes: bool) -> Dict[str, Any]:
    """Delete the data of ``targets``. A dry run unless ``yes``."""
    plan = plan_clear(project, targets)
    deleted = []
    if yes:
        for item in plan:
            path = item["path"]
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            deleted.append(str(path))
    return {"dry_run": not yes, "plan": plan, "deleted": deleted}


def export_memories(project: Path, out: Path) -> int:
    """Write all memories to a JSON file (vectors left out). Returns the count."""
    import lancedb

    store = paths.project_store(Path(project).resolve())
    lance = store / "lancedb"
    if not (lance / MEMORIES_TABLE).exists():
        raise ValueError(f"No memories found under {lance}.")
    table = lancedb.connect(str(lance)).open_table("memories")
    arrow = table.to_arrow()
    keep = [name for name in arrow.column_names if name != "vector"]
    rows = arrow.select(keep).to_pylist()
    Path(out).write_text(json.dumps(rows, indent=2, default=str))
    return len(rows)
