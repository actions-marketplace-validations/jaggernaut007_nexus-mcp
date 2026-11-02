"""The Grep and Read baseline: rank files the way an agent greps, and price the reading."""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from nexus_mcp.engines.live_grep import _GREP_SKIP_DIRS
from nexus_mcp.formatting.token_budget import TokenBudget

MAX_FILES_READ = 3
MAX_LINES_PER_READ = 2000
MAX_LISTED_FILES = 50
GREP_TIMEOUT_S = 20
STOPWORDS = frozenset(
    "the and for with that this from into when what where how are was not you your all any "
    "can its has have than then them they there their out one use used uses using per via".split()
)


def keywords(query: str) -> List[str]:
    """Distinct lowercase words of the query that an agent would grep for."""
    seen: List[str] = []
    for word in re.findall(r"[A-Za-z0-9_]+", query.lower()):
        if len(word) >= 3 and word not in STOPWORDS and word not in seen:
            seen.append(word)
    return seen


def _files_matching(root: Path, word: str) -> List[str]:
    """Files under ``root`` whose text contains ``word`` (case-insensitive, fixed string)."""
    rg, grep = shutil.which("rg"), shutil.which("grep")
    if rg:
        cmd = [rg, "-l", "-i", "-F", "--no-messages", "--color", "never", "-e", word, "."]
    elif grep:
        cmd = [grep, "-rlIiF"]
        for skip in _GREP_SKIP_DIRS:
            cmd.append(f"--exclude-dir={skip}")
        cmd += ["-e", word, "."]
    else:
        return []
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True, timeout=GREP_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        line[2:] if line.startswith("./") else line for line in proc.stdout.splitlines() if line
    ]


def rank(root: Path, query: str) -> Tuple[List[str], str]:
    """Files ranked by how many distinct query words they contain, and the listing text.

    The listing text stands for what the agent sees from its grep calls.
    """
    counts: Dict[str, int] = {}
    for word in keywords(query):
        for path in _files_matching(root, word):
            counts[path] = counts.get(path, 0) + 1
    ranked = sorted(counts, key=lambda p: (-counts[p], p))
    listing = "\n".join(ranked[:MAX_LISTED_FILES])
    return ranked, listing


def read_cost(root: Path, files: Sequence[str]) -> int:
    """Tokens to read ``files`` in full (each capped at ``MAX_LINES_PER_READ`` lines)."""
    budget = TokenBudget()
    total = 0
    for rel in files:
        try:
            with open(Path(root) / rel, encoding="utf-8", errors="replace") as handle:
                text = "".join(line for _, line in zip(range(MAX_LINES_PER_READ), handle))
        except OSError:
            continue
        total += budget.estimate_tokens(text)
    return total


def tokens_to_answer(root: Path, ranked: Sequence[str], listing: str, relevant: str) -> int:
    """Tokens that the agent uses before it has read the defining file.

    The agent reads files in rank order and stops at the right one, or after
    ``MAX_FILES_READ`` files when the right one is not found.
    """
    budget = TokenBudget()
    read: List[str] = []
    for rel in ranked[:MAX_FILES_READ]:
        read.append(rel)
        if rel == relevant:
            break
    return budget.estimate_tokens(listing) + read_cost(root, read)
