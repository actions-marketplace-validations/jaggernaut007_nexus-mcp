"""Prepare a benchmark repo for the `gitnexus` condition, with a pinned GitNexus release.

    python -m benchmarks.setup_gitnexus --repo chromium-base

The script does the four steps of the `gitnexus` condition (see benchmarks/README.md):

1. It checks that the installed `gitnexus` is GITNEXUS_VERSION. It does not install it.
   Install it yourself: `npm install -g gitnexus@<GITNEXUS_VERSION>`.
2. It runs `gitnexus analyze` in the repo, with the full-text search extension.
3. It writes the `<!-- gitnexus:start -->` block of the new `CLAUDE.md` to
   `benchmarks/repos/<repo>.gitnexus-context.md`. The runner gives this block to the agent.
4. It removes the `CLAUDE.md`, `AGENTS.md` and `.claude/skills/gitnexus-*` that `analyze`
   wrote, so the other conditions do not see them. A file that was in the repo before
   keeps its own text: only the GitNexus block is removed from it.

The GitNexus index (`.gitnexus/` in the repo) stays. `gitnexus mcp` reads it.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

from benchmarks.conditions import gitnexus_context_path

GITNEXUS_VERSION = "1.6.12"
BLOCK_START, BLOCK_END = "<!-- gitnexus:start -->", "<!-- gitnexus:end -->"
GENERATED_FILES = ("CLAUDE.md", "AGENTS.md")
SKILLS_DIR = Path(".claude") / "skills"
REPOS_DIR = Path(__file__).resolve().parent / "repos"

Runner = Callable[..., subprocess.CompletedProcess]


class SetupError(RuntimeError):
    """A step of the setup failed. The message says what to do."""


def installed_version(run: Runner = subprocess.run) -> Optional[str]:
    """The version that `gitnexus --version` prints, or None when gitnexus is not installed."""
    try:
        out = run(["gitnexus", "--version"], capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def extract_block(text: str) -> str:
    """The GitNexus block of a CLAUDE.md, with its markers and a final newline."""
    start = text.find(BLOCK_START)
    end = text.find(BLOCK_END, start + 1) if start >= 0 else -1
    if start < 0 or end < 0:
        raise SetupError(f"no {BLOCK_START} ... {BLOCK_END} block in the CLAUDE.md of analyze")
    return text[start : end + len(BLOCK_END)] + "\n"


def strip_block(text: str) -> str:
    """The text without its GitNexus block. Text with no block comes back as it is."""
    try:
        block = extract_block(text).rstrip("\n")
    except SetupError:
        return text
    rest = text.replace(block, "")
    return rest.strip("\n") + "\n" if rest.strip() else ""


def existing_skills(repo_dir: Path) -> List[str]:
    """The names of the skill folders in the repo, before analyze runs."""
    skills = repo_dir / SKILLS_DIR
    return sorted(p.name for p in skills.iterdir()) if skills.is_dir() else []


def remove_generated(repo_dir: Path, files_before: List[str], skills_before: List[str]) -> None:
    """Remove what analyze added. Keep the files and skills that were there before."""
    for name in GENERATED_FILES:
        path = repo_dir / name
        if not path.exists():
            continue
        if name in files_before:
            path.write_text(strip_block(path.read_text()))
        else:
            path.unlink()
    skills = repo_dir / SKILLS_DIR
    if skills.is_dir():
        for folder in skills.iterdir():
            if folder.name.startswith("gitnexus-") and folder.name not in skills_before:
                shutil.rmtree(folder)
        # Remove the folders that analyze made and that are now empty.
        for folder in (skills, skills.parent):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()


def setup(repo_dir: Path, run: Runner = subprocess.run) -> Path:
    """Run analyze on repo_dir and write its context block. Returns the context file path."""
    version = installed_version(run)
    if version != GITNEXUS_VERSION:
        found = f"found {version}" if version else "gitnexus is not installed"
        raise SetupError(
            f"the benchmark pins gitnexus {GITNEXUS_VERSION} ({found}). "
            f"Install it with: npm install -g gitnexus@{GITNEXUS_VERSION}"
        )
    files_before = [n for n in GENERATED_FILES if (repo_dir / n).exists()]
    skills_before = existing_skills(repo_dir)

    env = {**os.environ, "GITNEXUS_LBUG_EXTENSION_INSTALL": "auto"}
    result = run(["gitnexus", "analyze", "."], cwd=repo_dir, env=env)
    if result.returncode != 0:
        raise SetupError(f"gitnexus analyze failed with exit code {result.returncode}")

    claude_md = repo_dir / "CLAUDE.md"
    if not claude_md.exists():
        raise SetupError("gitnexus analyze wrote no CLAUDE.md, so there is no context block")
    context = gitnexus_context_path(repo_dir)
    context.write_text(extract_block(claude_md.read_text()))
    remove_generated(repo_dir, files_before, skills_before)
    return context


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--repo", help="a repo name under benchmarks/repos/")
    where.add_argument("--repo-dir", type=Path, help="the path of any repo")
    args = parser.parse_args(argv)
    repo_dir = (args.repo_dir or REPOS_DIR / args.repo).resolve()
    if not repo_dir.is_dir():
        print(f"[gitnexus] no repo at {repo_dir}", file=sys.stderr)
        return 1
    try:
        context = setup(repo_dir)
    except SetupError as exc:
        print(f"[gitnexus] {exc}", file=sys.stderr)
        return 1
    print(f"[gitnexus] index ready in {repo_dir / '.gitnexus'}; context block in {context}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
