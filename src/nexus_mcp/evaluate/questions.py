"""Make eval questions from the user's own code.

The ground truth is the file that defines a symbol. It comes from the parser and never
from the ranking of any search engine, so neither side of the comparison grades itself.
"""

import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from nexus_mcp.engines.fusion import is_test_path

KINDS = ("concept", "described", "lookup")
SYMBOL_TYPES = ("function", "class", "method")
MIN_DESCRIBED_WORDS = 2
MIN_DOC_WORDS = 4

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


@dataclass(frozen=True)
class Question:
    id: str
    kind: str
    query: str
    relevant: str  # path of the defining file, relative to the project root
    symbol: str


def split_words(name: str) -> List[str]:
    """``parseConfigFile`` and ``parse_config_file`` both give ``['parse', 'config', 'file']``."""
    parts = []
    for chunk in re.split(r"[^A-Za-z0-9]+", name):
        parts.extend(_CAMEL.split(chunk))
    return [p.lower() for p in parts if p]


def first_sentence(doc: str) -> str:
    """First sentence of a docstring, on one line."""
    text = " ".join((doc or "").strip().split())
    match = re.search(r"(?<=[.!?])\s", text)
    return text[: match.start()] if match else text


def _relative(filepath: str, root: Path) -> str:
    try:
        return str(Path(filepath).resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return filepath


def _candidates(rows: Sequence[Dict[str, Any]], root: Path) -> List[Dict[str, Any]]:
    """Symbols that make a fair question: source code, one definition, a usable name."""
    files_by_name: Dict[str, set] = {}
    for row in rows:
        files_by_name.setdefault(row["symbol_name"], set()).add(row["filepath"])
    out = []
    for row in rows:
        name = row.get("symbol_name") or ""
        if row.get("symbol_type") not in SYMBOL_TYPES:
            continue
        if name.startswith("__") or len(name) < 3:
            continue
        rel = _relative(row["filepath"], root)
        if is_test_path(rel) or len(files_by_name[name]) > 1:
            continue
        out.append({**row, "rel": rel})
    out.sort(key=lambda r: (r["rel"], r.get("line_start", 0), r["symbol_name"]))
    return out


def _build(row: Dict[str, Any], kind: str) -> Question | None:
    name = row["symbol_name"]
    words = split_words(name)
    if kind == "lookup":
        query = name
    elif kind == "described":
        if len(words) < MIN_DESCRIBED_WORDS:
            return None
        parent_words = split_words(row.get("parent") or "")
        query = " ".join(parent_words + words)
    else:  # concept
        sentence = first_sentence(row.get("docstring") or "")
        sentence = re.sub(re.escape(name), "", sentence, flags=re.IGNORECASE).strip()
        if len(sentence.split()) < MIN_DOC_WORDS:
            return None
        query = sentence
    return Question(id=f"{kind}-{name}", kind=kind, query=query, relevant=row["rel"], symbol=name)


def generate(
    rows: Sequence[Dict[str, Any]], root: Path, count: int = 30, seed: int = 7
) -> List[Question]:
    """Up to ``count`` questions, spread over the kinds that the code can support.

    The sample is seeded, so the same code and seed give the same questions.
    """
    rng = random.Random(seed)
    pool = _candidates(rows, root)
    rng.shuffle(pool)
    per_kind: Dict[str, List[Question]] = {k: [] for k in KINDS}
    used = set()
    for kind in KINDS:
        for row in pool:
            if (kind, row["symbol_name"]) in used:
                continue
            question = _build(row, kind)
            if question:
                per_kind[kind].append(question)
                used.add((kind, row["symbol_name"]))
    available = [k for k in KINDS if per_kind[k]]
    chosen: List[Question] = []
    if not available:
        return chosen
    share = max(1, count // len(available))
    for kind in available:
        chosen.extend(per_kind[kind][:share])
    for kind in available:  # top up to `count` when a kind ran short
        extra = per_kind[kind][share:]
        while len(chosen) < count and extra:
            chosen.append(extra.pop(0))
    return chosen[:count]
