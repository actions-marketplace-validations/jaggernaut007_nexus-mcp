"""Per-language benchmark corpora: load the manifest and make pinned checkouts.

    python -m benchmarks.corpora list
    python -m benchmarks.corpora setup                  # every corpus
    python -m benchmarks.corpora setup --languages C#,PHP

Each corpus is a shallow, sparse checkout of one commit under benchmarks/repos/<name>.
Agent config files of the corpus (CLAUDE.md, .claude/, .mcp.json) are deleted after the
checkout, so neither benchmark condition gets instructions that the other does not get.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

BENCH_DIR = Path(__file__).resolve().parent
MANIFEST = BENCH_DIR / "corpora.yaml"
REPOS_DIR = BENCH_DIR / "repos"
TIERS = ("quick", "retrieval", "graph", "live")
# Files that Claude Code reads on its own. A corpus must not bring them into a run.
AGENT_CONFIG = ("CLAUDE.md", "CLAUDE.local.md", ".claude", ".mcp.json")


# The rows of a graph-coverage result (evals/graph_coverage.py) that belong to a corpus
# language. A corpus holds files of other languages too (Chromium `base/` has Java, Python
# and Rust), and their nodes must not count for it. A C or C++ `.h` file can be either.
GRAPH_KEYS = {
    "python": ("python",),
    "typescript": ("typescript", "tsx"),
    "javascript": ("javascript",),
    "java": ("java",),
    "c#": ("csharp",),
    "php": ("php",),
    "c++": ("cpp", "c"),
    "c": ("c", "cpp"),
    "go": ("go",),
    "rust": ("rust",),
}


def graph_totals(graph: Dict[str, Any], language: str) -> Tuple[int, int]:
    """(function nodes, CALLS edges) of the rows of ``language`` in a graph-coverage result.

    An unknown language falls back to every row.
    """
    keys = GRAPH_KEYS.get(language.strip().lower())
    rows = [v for k, v in graph.items() if keys is None or k in keys]
    return sum(v["functions"] for v in rows), sum(v["calls"] for v in rows)


def load(path: Path = MANIFEST) -> List[Dict[str, Any]]:
    """The corpora of the manifest. Raises ValueError on a bad entry."""
    corpora = yaml.safe_load(path.read_text())["corpora"]
    names = set()
    for c in corpora:
        for key in ("name", "language", "url", "pin", "tiers"):
            if not c.get(key):
                raise ValueError(f"corpus {c.get('name')!r} has no {key!r}")
        if c["name"] in names:
            raise ValueError(f"corpus {c['name']!r} is listed twice")
        names.add(c["name"])
        unknown = set(c["tiers"]) - set(TIERS)
        if unknown:
            raise ValueError(f"corpus {c['name']!r} has unknown tiers {sorted(unknown)}")
        if "live" in c["tiers"] and not c.get("tasks"):
            raise ValueError(f"corpus {c['name']!r} is in the live tier but has no tasks")
        c.setdefault("sparse", [])
    return corpora


def select(
    corpora: List[Dict[str, Any]],
    languages: Optional[Iterable[str]] = None,
    tier: Optional[str] = None,
    names: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """The corpora of the given languages (case-insensitive), tier and names. None: all."""
    wanted = {lang.strip().lower() for lang in languages} if languages else None
    named = {n.strip() for n in names} if names else None
    return [
        c for c in corpora
        if (wanted is None or c["language"].lower() in wanted)
        and (tier is None or tier in c["tiers"])
        and (named is None or c["name"] in named)
    ]


def repo_dir(corpus: Dict[str, Any], repos_dir: Path = REPOS_DIR) -> Path:
    """The local checkout folder of one corpus."""
    return repos_dir / corpus["name"]


def _git(args: List[str], cwd: Path) -> str:
    out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {out.stderr.strip()}")
    return out.stdout.strip()


def head(path: Path) -> Optional[str]:
    """The checked-out commit of `path`, or None when it is not a git checkout."""
    if not (path / ".git").exists():
        return None
    try:
        return _git(["rev-parse", "HEAD"], path)
    except RuntimeError:
        return None


def strip_agent_config(path: Path) -> List[str]:
    """Delete the agent config files at every depth of a checkout.

    Claude Code also loads a `CLAUDE.md` of a subfolder when it reads files there, so a
    nested one could give the benchmark agent instructions. Returns the deleted paths,
    relative to ``path``.
    """
    removed = []
    targets = sorted(
        (p for name in AGENT_CONFIG for p in path.rglob(name) if ".git" not in p.parts),
        key=lambda p: len(p.parts),
    )
    for target in targets:
        if not target.exists() and not target.is_symlink():
            continue  # inside a folder deleted before it
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        removed.append(target.relative_to(path).as_posix())
    return removed


def checkout(corpus: Dict[str, Any], repos_dir: Path = REPOS_DIR) -> Path:
    """Make the pinned, sparse, shallow checkout of one corpus. Idempotent."""
    dest = repo_dir(corpus, repos_dir)
    if head(dest) == corpus["pin"]:
        strip_agent_config(dest)
        print(f"[corpora] {corpus['name']} already at {corpus['pin'][:10]}", file=sys.stderr)
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    print(f"[corpora] {corpus['name']} @ {corpus['pin'][:10]}", file=sys.stderr)
    _git(["init", "-q"], dest)
    _git(["remote", "add", "origin", corpus["url"]], dest)
    if corpus["sparse"]:
        _git(["sparse-checkout", "set", "--cone", *corpus["sparse"]], dest)
    _git(["fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", corpus["pin"]], dest)
    _git(["checkout", "-q", "--detach", "FETCH_HEAD"], dest)
    if head(dest) != corpus["pin"]:
        raise RuntimeError(f"{corpus['name']}: checkout is not at {corpus['pin']}")
    strip_agent_config(dest)
    return dest


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: `list` the corpora, or `setup` (clone) them."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "setup"])
    parser.add_argument("--languages", default="", help="Comma-separated languages")
    parser.add_argument("--tier", choices=TIERS, default=None)
    args = parser.parse_args(argv)
    langs = [s for s in args.languages.split(",") if s.strip()] or None
    chosen = select(load(), langs, args.tier)
    if args.command == "list":
        for c in chosen:
            print(f"{c['language']:<11} {c['name']:<16} {c['pin'][:10]}  {','.join(c['tiers'])}")
        return 0
    for c in chosen:
        checkout(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
