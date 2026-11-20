"""Keep the index folder out of git with a line in ``.git/info/exclude``.

The index holds code and can be about 1 GB. A user who does not know the folder can
commit and push it. ``.git/info/exclude`` is local to one clone and git never tracks it,
so the tracked ``.gitignore`` of the user does not change.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_FOLDER_NAME = ".nexus"
COMMENT = "# Nexus-MCP index folder. Added by nexus-mcp-ci. NEXUS_GIT_EXCLUDE=false stops this."

_GIT_TIMEOUT_S = 10
# What git says (with LC_ALL=C) when a folder is not in a work tree. This is not an error.
_NO_WORK_TREE = ("not a git repository", "must be run in a work tree")
# Variables that point git at another repo. A server that a git hook starts inherits them.
_GIT_ENV_DROP = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)


def ensure_git_exclude(storage_path: Path) -> Optional[Path]:
    """Add the index folder to ``.git/info/exclude`` of the repo that holds it.

    Returns the exclude file when this call wrote a line, or None when there was nothing
    to do: the folder is not in a git work tree, or an ignore rule covers it already.
    It never raises. An index run must not fail because of this step.
    """
    try:
        return _ensure(Path(storage_path))
    except Exception as e:  # noqa: BLE001 - see the docstring
        logger.warning(
            "Could not add the index folder %s to the git exclude file: %s. "
            "Add it to .gitignore, so that git does not commit the index.",
            storage_path, e,
        )
        return None


def _ensure(storage_path: Path) -> Optional[Path]:
    storage = storage_path.expanduser().resolve()
    if not storage.is_dir() or not _has_git_entry(storage):
        return None

    # One call gives the root of the work tree, the folder path from that root, and the
    # exclude file. In a linked worktree, `--git-path` gives the file in the common git dir.
    found = _git(
        storage, "rev-parse", "--show-toplevel", "--show-prefix", "--git-path", "info/exclude"
    )
    lines = found.stdout.splitlines()
    if found.returncode != 0 or len(lines) != 3:
        error = found.stderr.strip()
        if any(text in error for text in _NO_WORK_TREE):
            return None  # For example a folder inside `.git`, or a stray `.git` entry.
        raise RuntimeError(f"git rev-parse failed: {error}")
    top, prefix, exclude_text = Path(lines[0]), lines[1].rstrip("/"), lines[2]
    if not prefix:
        return None  # The index folder is the root of the work tree.
    exclude = Path(exclude_text)
    if not exclude.is_absolute():
        exclude = storage / exclude

    # `--no-index`: a folder that is tracked already still counts as covered by its rule.
    check = _git(top, "check-ignore", "--quiet", "--no-index", "--", prefix)
    if check.returncode == 0:
        return None
    if check.returncode != 1:
        raise RuntimeError(f"git check-ignore failed: {check.stderr.strip()}")

    pattern = _pattern(prefix)
    old = exclude.read_bytes().decode("utf-8", errors="replace") if exclude.exists() else ""
    if pattern in (line.strip() for line in old.splitlines()):
        # The line is there, but a later rule (`!.nexus/`) overrides it. Respect that rule.
        return None

    exclude.parent.mkdir(parents=True, exist_ok=True)
    separator = "" if not old or old.endswith("\n") else "\n"
    with open(exclude, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{separator}{COMMENT}\n{pattern}\n")
    logger.info("Added %s to %s, so that git does not commit the index.", pattern, exclude)
    return exclude


def _has_git_entry(folder: Path) -> bool:
    """True when `folder` or a parent holds `.git`. Costs no git process outside a repo."""
    return any((parent / ".git").exists() for parent in (folder, *folder.parents))


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if key not in _GIT_ENV_DROP}
    env["LC_ALL"] = "C"  # English messages, for the `_NO_WORK_TREE` check.
    # stdin is the JSON-RPC stream of the MCP client. A child process must not read it.
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        env=env,
        check=False,
    )


def _pattern(prefix: str) -> str:
    """The exclude line for the folder at `prefix` (its path from the root of the work tree).

    The default folder name gives `.nexus/`, which also fits the index of a sub-project.
    A custom folder gives a path from the root (`/build/index/`), so no other folder fits.
    """
    if prefix.rsplit("/", 1)[-1] == DEFAULT_FOLDER_NAME:
        return f"{DEFAULT_FOLDER_NAME}/"
    escaped = "".join(f"\\{char}" if char in "\\*?[ " else char for char in prefix)
    return f"/{escaped}/"
