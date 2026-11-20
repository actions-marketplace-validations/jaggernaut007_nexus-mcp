"""Attach the linked repos of a project to the server session, read-only (ADR-030).

Each linked repo keeps its own store in `<repo>/.nexus`. The server opens it for reading
and never changes it: only `nexus-mcp-ci link` and `link --refresh` write to it. The
registry that names the links is a user-level file (see links.py). The store itself is not
trusted: its roots and its repo_id must match the registry before it is attached.

A link that cannot be attached is kept in the list with a `skip_reason`, so that `status`
can say why. A skipped link never stops the primary project.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from nexus_mcp import links

logger = logging.getLogger(__name__)

REFRESH_HINT = "Run `nexus-mcp-ci link --refresh`."


@dataclass
class LinkedRepo:
    """One linked repo in a session. `skip_reason` is None when it is attached."""

    name: str
    path: Path
    repo_id: str
    skip_reason: Optional[str] = None
    pipeline: Any = None  # opened for reading only: validate, restore the graph, check staleness
    files: Optional[int] = None
    _staleness: Optional[dict] = field(default=None, repr=False)
    _staleness_checked_at: float = field(default=0.0, repr=False)

    @property
    def codebase_path(self) -> Path:
        """The root of this repo. With `codebase_paths` it lets the helpers of `core_api`
        that take a session state (path display, code reads) take a linked repo too."""
        return self.path

    @property
    def codebase_paths(self) -> List[Path]:
        """The roots of this repo: a list with the one root, as `codebase_path`."""
        return [self.path]

    @property
    def attached(self) -> bool:
        """True when the stores of this repo are open (it was not skipped)."""
        return self.pipeline is not None

    @property
    def vector_engine(self):
        return self.pipeline.vector_engine if self.pipeline else None

    @property
    def bm25_engine(self):
        return self.pipeline.bm25_engine if self.pipeline else None

    @property
    def graph_engine(self):
        return self.pipeline.graph_engine if self.pipeline else None


def _skipped(link: links.Link, reason: str) -> LinkedRepo:
    logger.warning("Linked repo %r (%s) is not attached: %s", link.name, link.path, reason)
    return LinkedRepo(link.name, Path(link.path), link.repo_id, skip_reason=reason)


def _read_metadata(metadata_path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(metadata_path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("mtimes"), dict) else None


def stored_roots_match(meta: Dict[str, Any], path: Path) -> bool:
    """True when the store was built for exactly the folder that the registry names."""
    raw = meta.get("codebase_paths") or [meta.get("codebase_path")]
    if not isinstance(raw, list):
        return False
    try:
        roots = {str(Path(p).resolve()) for p in raw if isinstance(p, str) and p}
    except (OSError, ValueError):
        return False
    return len(roots) == len(raw) and roots == {str(path.resolve())}


def check_store(link: links.Link, settings) -> Optional[str]:
    """A reason why the store of `link` cannot be attached, or None. It reads files only."""
    path = Path(link.path)
    if not path.is_dir():
        return "the folder is gone"
    store = links.store_dir(path)
    metadata_path = store / "index_metadata.json"
    if not metadata_path.exists() or not (store / "lancedb").is_dir():
        return f"the repo has no index. {REFRESH_HINT}"
    meta = _read_metadata(metadata_path)
    if meta is None:
        return f"the index metadata is corrupt. {REFRESH_HINT}"
    stored_model = meta.get("embedding_model")
    if stored_model and stored_model != settings.embedding_model:
        return (
            f"the embedding model differs (index: {stored_model}, "
            f"this project: {settings.embedding_model}). Set NEXUS_EMBEDDING_MODEL to the same "
            f"value for the server and for the command line. {REFRESH_HINT}"
        )
    if not stored_roots_match(meta, path):
        return (
            "the index was built for a different folder. "
            "Delete its .nexus folder and link again."
        )
    stored_id = meta.get("repo_id")
    if not stored_id:
        return f"the index has no repo id. {REFRESH_HINT}"
    if stored_id != link.repo_id:
        return (
            "the repo id changed: the folder holds another repo, or its index was built again "
            f"from scratch. Run `nexus-mcp-ci link {link.path}`."
        )
    return None


def _attach_one(link: links.Link, base_settings) -> LinkedRepo:
    from nexus_mcp.indexing.pipeline import IndexingPipeline

    path = Path(link.path)
    try:
        reason = check_store(link, base_settings)
        if reason:
            return _skipped(link, reason)
        pipeline = IndexingPipeline(links.settings_for(path, base_settings), read_only=True)
        if not pipeline._validate_index():
            return _skipped(link, f"the index is corrupt or from another version. {REFRESH_HINT}")
        if not pipeline._restore_graph(read_only=True):
            return _skipped(
                link, f"the saved graph is missing or older than the index. {REFRESH_HINT}"
            )
        meta = _read_metadata(links.store_dir(path) / "index_metadata.json") or {}
    except Exception as exc:  # noqa: BLE001 - one bad store must not stop the others
        return _skipped(link, f"the index could not be opened ({exc}). {REFRESH_HINT}")
    logger.info("Attached linked repo %r (%s)", link.name, link.path)
    return LinkedRepo(
        link.name, path, link.repo_id, pipeline=pipeline, files=len(meta.get("mtimes", {}))
    )


def attach_all(primary: Path, base_settings) -> List[LinkedRepo]:
    """Open the linked repos of the project at `primary`. [] when it has no link."""
    return [_attach_one(link, base_settings) for link in links.links_for(primary)]


def staleness(repo: LinkedRepo, interval_s: float) -> Dict[str, Any]:
    """Whether files of a linked repo changed since its last index.

    Read only. The result is kept for `interval_s`, as for the primary project, because
    the check walks the folder.
    """
    if repo.pipeline is None:
        return {"stale": False, "changed_files": 0, "reason": None}
    now = time.monotonic()
    if repo._staleness is not None and now - repo._staleness_checked_at < interval_s:
        return repo._staleness
    try:
        result = repo.pipeline.check_staleness([repo.path])
    except Exception as exc:  # noqa: BLE001 - a status call must not fail for a linked repo
        logger.warning("Could not check linked repo %r: %s", repo.name, exc)
        result = {"stale": False, "changed_files": 0, "reason": None}
    repo._staleness, repo._staleness_checked_at = result, now
    return result


def describe(repo: LinkedRepo, interval_s: float) -> Dict[str, Any]:
    """The entry of `status` for one linked repo."""
    return {
        "name": repo.name,
        "path": str(repo.path),
        "files": repo.files,
        "stale": staleness(repo, interval_s)["stale"],
        "skipped": repo.skip_reason,
    }
