"""Resolve what to evaluate: a local path, a public repo URL, or the bundled example."""

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from nexus_mcp.evaluate import paths

URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+/[\w.\-]+/[\w.\-]+?(?:\.git)?/?$")
SCP_RE = re.compile(r"^[\w.-]+@[\w.-]+:")  # git@github.com:owner/repo.git
CLONE_TIMEOUT_S = 600


@dataclass
class Source:
    kind: str  # "path", "url" or "example"
    root: Path
    label: str  # shown in the report: the URL, or "example", or the folder name
    sha: Optional[str] = None
    cleanup: Optional[Path] = None  # temp folder to delete after the run


def example_dir() -> Optional[Path]:
    """The example project: the installed Nexus-MCP source code itself.

    It ships in every install, needs no network, and is large enough (about 50 files) for a
    fair comparison. A tiny toy project makes Grep and Read look better than they are.
    """
    package = Path(__file__).resolve().parent.parent
    return package if (package / "server.py").is_file() else None


def is_url(text: str) -> bool:
    return bool(URL_RE.match(text))


def _slug(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("://", 1)[1].removesuffix(".git").rstrip("/"))


def clone(url: str, run=subprocess.run) -> Source:
    """Shallow clone of a public repo into the eval cache.

    This is a network step: the caller says so before it runs.
    """
    dest = paths.eval_cache() / "repos" / _slug(url)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = run(
        ["git", "clone", "--depth", "1", "--quiet", "--", url, str(dest)],
        capture_output=True,
        text=True,
        timeout=CLONE_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {(proc.stderr or '').strip()[:300]}")
    sha = run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30
    ).stdout.strip()
    return Source("url", dest, url, sha or None)


def resolve(arg: Optional[str], example: bool, run=subprocess.run) -> Source:
    """Turn the CLI argument into a Source. Raises ValueError for a bad argument."""
    if example:
        src = example_dir()
        if src is None:
            raise ValueError("The Nexus-MCP source is missing from this install.")
        tmp = Path(tempfile.mkdtemp(prefix="nexus-example-"))
        target = tmp / "nexus_mcp"
        shutil.copytree(src, target, ignore=shutil.ignore_patterns(".nexus", "__pycache__"))
        return Source("example", target, "example (Nexus-MCP source)", cleanup=tmp)
    text = arg or "."
    if "://" in text or SCP_RE.match(text):
        if not is_url(text):
            raise ValueError(
                "Only https:// repo URLs are accepted, for example https://github.com/pallets/flask"
            )
        return clone(text, run=run)
    root = Path(text).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Not a folder: {text}")
    return Source("path", root, root.name)
