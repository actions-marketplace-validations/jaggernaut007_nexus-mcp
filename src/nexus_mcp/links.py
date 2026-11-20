"""The registry of linked repos (ADR-030).

A project can link other repos. Each repo keeps its own index in `<repo>/.nexus`. The
links are kept in one user-level file, outside every repo, because a clone or an archive
can supply the `.nexus` folder of a repo, and the server does not trust its contents
(see `core_api._stored_roots`). Only a person who runs `nexus-mcp-ci link` writes this file.

File shape (version 1)::

    {"version": 1,
     "links": {"<primary root>": [{"name": ..., "path": ..., "repo_id": ...}, ...]}}

The key is the resolved root of the project that owns the links. This module has no
engine or model import, so reading the registry is cheap.
"""

import copy
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from nexus_mcp.evaluate import paths

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 1
# The name is the first part of a result path (`backend/src/app.py`), so it holds one safe
# path segment only.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class LinksError(Exception):
    """A link cannot be read, changed or written. The message tells the person what to do."""


@dataclass(frozen=True)
class Link:
    """One linked repo of a project."""

    name: str
    path: str
    repo_id: str

    def to_dict(self) -> Dict[str, str]:
        """Return the registry entry of this link: `name`, `path` and `repo_id`."""
        return {"name": self.name, "path": self.path, "repo_id": self.repo_id}


def registry_path() -> Path:
    """The registry file. `NEXUS_LINKS_FILE` moves it."""
    return paths.links_file()


def store_dir(path: Path) -> Path:
    """The index folder of a linked repo: always `<repo>/.nexus`.

    `NEXUS_STORAGE_DIR` is the setting of the primary project. It must not move the store of
    another repo, so it is not used here.
    """
    return Path(path) / ".nexus"


def settings_for(path: Path, base=None):
    """Settings for the store of the repo at `path`.

    The other values come from `base` (a copy of it), or from the environment when `base`
    is None. A linked repo must use the embedding model of the project that links it.
    """
    from nexus_mcp.config import Settings

    settings = copy.copy(base) if base is not None else Settings()
    settings.storage_dir = str(store_dir(path))
    settings.storage_base = ""
    return settings


def _key(primary: Path) -> str:
    return str(Path(primary).resolve())


def default_name(path: Path) -> str:
    """A safe link name from a folder name: other characters become `-`."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(path).resolve().name).lstrip("._-")
    return name[:64] or "repo"


def validate_name(name: str) -> str:
    """Return `name` when it is safe as a path segment. Raise LinksError otherwise."""
    if not _NAME.fullmatch(name or ""):
        raise LinksError(
            f"The name {name!r} is not valid. Use 1 to 64 letters, digits, '.', '_' or '-', "
            "and start with a letter or a digit."
        )
    return name


def _entries(raw: object) -> List[Link]:
    """The valid links of one list. An entry that is not valid is left out."""
    if not isinstance(raw, list):
        return []
    found = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name, path, repo_id = item.get("name"), item.get("path"), item.get("repo_id")
        if (
            isinstance(name, str)
            and isinstance(path, str)
            and isinstance(repo_id, str)
            and _NAME.fullmatch(name)
            and path
        ):
            found.append(Link(name, path, repo_id))
    return found


def _load(file: Path) -> Dict[str, List[Link]]:
    """Every link of every project. Raises LinksError when the file cannot be read."""
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text())
    except (OSError, ValueError) as exc:
        raise LinksError(
            f"The link registry {file} is not valid JSON ({exc}). Fix or delete it."
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("links"), dict):
        raise LinksError(f"The link registry {file} has an unexpected shape. Fix or delete it.")
    return {key: _entries(value) for key, value in data["links"].items() if isinstance(key, str)}


def _save(file: Path, registry: Dict[str, List[Link]]) -> None:
    """Write the registry in one step: a temporary file in the same folder, then a rename.

    A reader, such as a server that starts at the same time, sees the old file or the new
    one, never half of one. A failure leaves the old file as it was.
    """
    payload = {
        "version": REGISTRY_VERSION,
        "links": {
            key: [link.to_dict() for link in value] for key, value in registry.items() if value
        },
    }
    tmp: Optional[str] = None
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=".links-", suffix=".tmp")
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(payload, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, file)
        tmp = None
    except OSError as exc:
        raise LinksError(f"Could not write the link registry {file}: {exc}") from exc
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def links_for(primary: Path) -> List[Link]:
    """The links of the project at `primary`. Empty when there is none or the file is broken.

    The server calls this at start. A broken registry must not stop it, so the error is
    logged and the project runs without links.
    """
    try:
        return list(_load(registry_path()).get(_key(primary), []))
    except LinksError as exc:
        logger.warning("Linked repos are off: %s", exc)
        return []


def plan_link(primary: Path, path: Path, name: Optional[str] = None) -> Link:
    """Check a link and return it, with an empty repo_id. Writes nothing.

    `nexus-mcp-ci link` calls this before it indexes the repo, so that a wrong path or a
    taken name fails first. Linking the same path again keeps its name.
    """
    key = _key(primary)
    target = Path(path).resolve()
    if not target.is_dir():
        raise LinksError(f"{path} is not a folder.")
    if str(target) == key:
        raise LinksError("A project cannot link itself.")
    if name is not None:
        validate_name(name)

    current = _load(registry_path()).get(key, [])
    same_path = next((link for link in current if link.path == str(target)), None)
    if same_path is not None and name is not None and name != same_path.name:
        raise LinksError(
            f"{target} is already linked as {same_path.name!r}. "
            f"Run `nexus-mcp-ci unlink {same_path.name}` first to rename it."
        )
    chosen = name or (same_path.name if same_path else validate_name(default_name(target)))
    if any(link.name == chosen and link.path != str(target) for link in current):
        raise LinksError(f"The name {chosen!r} already names another link. Use --name.")
    return Link(chosen, str(target), "")


def add_link(
    primary: Path, path: Path, repo_id: str, name: Optional[str] = None
) -> Link:
    """Link the repo at `path` to the project at `primary`, or update the existing link.

    Linking the same path again keeps its name and updates the repo_id.
    """
    planned = plan_link(primary, path, name)
    file = registry_path()
    registry = _load(file)
    key = _key(primary)
    link = Link(planned.name, planned.path, repo_id)
    registry[key] = [x for x in registry.get(key, []) if x.path != link.path] + [link]
    _save(file, registry)
    return link


def remove_link(primary: Path, name: str) -> Link:
    """Remove the link `name` of the project at `primary`. The index of the repo stays."""
    key = _key(primary)
    file = registry_path()
    registry = _load(file)
    current = registry.get(key, [])
    found = next((link for link in current if link.name == name), None)
    if found is None:
        raise LinksError(f"No link named {name!r}. Run `nexus-mcp-ci links` to see the names.")
    registry[key] = [link for link in current if link is not found]
    _save(file, registry)
    return found
