"""The quick tier: Nexus-MCP against Grep and Read on questions from your own code.

It calls no LLM and sends nothing over the network. The index lives in a temporary
folder unless ``keep_index`` is set. The one network step is the first download of
the embedding model, and ``NEXUS_OFFLINE=1`` blocks it.
"""

import asyncio
import json
import os
import statistics
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from nexus_mcp.evaluate import baseline, questions
from nexus_mcp.formatting.token_budget import TokenBudget

TOP_FILES = 3
SIZE_BANDS = ((50, "<50 files"), (500, "50-500 files"), (5000, "500-5k files"))
_ENV = {"NEXUS_AUTO_WATCH": "false", "NEXUS_AUTO_RESTORE": "false", "NEXUS_WARM_START": "false"}


def size_band(files: int) -> str:
    for limit, label in SIZE_BANDS:
        if files < limit:
            return label
    return ">5k files"


@contextmanager
def isolated_nexus(storage: Path) -> Iterator[None]:
    """Point the global nexus session at ``storage``, then restore everything."""
    import nexus_mcp.core_api as core_api
    from nexus_mcp.config import reset_settings
    from nexus_mcp.state import reset_state

    env = {**_ENV, "NEXUS_STORAGE_DIR": str(storage)}
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    reset_settings()
    reset_state()
    core_api._pipeline = None
    try:
        yield
    finally:
        core_api._pipeline = None
        reset_state()
        reset_settings()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def read_symbol_rows(storage: Path) -> List[Dict[str, Any]]:
    """Symbols of the stored index: name, type, parent, docstring and defining file."""
    import lancedb

    table = lancedb.connect(str(storage / "lancedb")).open_table("chunks")
    wanted = [
        "filepath",
        "symbol_name",
        "symbol_type",
        "parent",
        "docstring",
        "line_start",
        "language",
    ]
    return table.to_arrow().select(wanted).to_pylist()


def _files_of(results: Sequence[Dict[str, Any]], root: Path) -> List[str]:
    """Distinct file paths of ranked search results, relative to ``root``, in rank order."""
    files: List[str] = []
    for item in results:
        raw = item.get("absolute_path") or item.get("filepath") or item.get("file")
        if not raw:
            continue
        path = Path(raw)
        try:
            rel = (
                str(path.resolve().relative_to(root.resolve())) if path.is_absolute() else str(path)
            )
        except ValueError:
            rel = str(path)
        if rel not in files:
            files.append(rel)
    return files


def _hit(files: Sequence[str], relevant: str, k: int) -> bool:
    return relevant in list(files)[:k]


def _median(values: Sequence[float]) -> float:
    return round(statistics.median(values), 1) if values else 0.0


def _summarise(rows: Sequence[Dict[str, Any]], condition: str) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {"questions": 0}
    return {
        "questions": n,
        "hit_at_1": round(sum(r[condition]["hit1"] for r in rows) / n, 3),
        "hit_at_3": round(sum(r[condition]["hit3"] for r in rows) / n, 3),
        "median_tokens": _median([r[condition]["tokens"] for r in rows]),
        "median_ms": _median([r[condition]["ms"] for r in rows]),
    }


def run_one(root: Path, question: questions.Question, search_fn) -> Dict[str, Any]:
    """Run one question through both conditions."""
    budget = TokenBudget()

    started = time.perf_counter()
    ranked, listing = baseline.rank(root, question.query)
    grep_ms = (time.perf_counter() - started) * 1000
    grep = {
        "hit1": _hit(ranked, question.relevant, 1),
        "hit3": _hit(ranked, question.relevant, TOP_FILES),
        "tokens": baseline.tokens_to_answer(root, ranked, listing, question.relevant),
        "ms": round(grep_ms, 1),
    }

    started = time.perf_counter()
    response = search_fn(question.query)
    nexus_ms = (time.perf_counter() - started) * 1000
    files = _files_of(response.get("results", []), root)
    tokens = budget.estimate_tokens(json.dumps(response))
    if not _hit(files, question.relevant, TOP_FILES):
        # The agent falls back to reading files, as in the baseline.
        tokens += baseline.read_cost(root, files[TOP_FILES : TOP_FILES + baseline.MAX_FILES_READ])
    nexus = {
        "hit1": _hit(files, question.relevant, 1),
        "hit3": _hit(files, question.relevant, TOP_FILES),
        "tokens": tokens,
        "ms": round(nexus_ms, 1),
    }
    return {"id": question.id, "kind": question.kind, "grep": grep, "nexus": nexus}


def verdict(summary: Dict[str, Any], files: int) -> Dict[str, Any]:
    """ "Strong fit", "Likely fit" or "Weak fit", with the reasons in plain words."""
    grep, nexus = summary["grep"], summary["nexus"]
    saving = 1 - nexus["median_tokens"] / grep["median_tokens"] if grep["median_tokens"] else 0.0
    gain = nexus["hit_at_3"] - grep["hit_at_3"]
    direction = "fewer" if saving >= 0 else "more"
    reasons = [
        f"Tokens to reach the right file: Nexus-MCP {nexus['median_tokens']:.0f}, "
        f"Grep and Read {grep['median_tokens']:.0f} "
        f"({abs(saving):.0%} {direction} with Nexus-MCP).",
        f"Right file in the top 3: {nexus['hit_at_3']:.0%} against {grep['hit_at_3']:.0%}.",
    ]
    if files < 50:
        label = "Weak fit"
        reasons.append(
            "The project is small (under 50 files). Grep and Read are already cheap here."
        )
    elif gain >= 0.10 and saving >= 0.30:
        label = "Strong fit"
    elif gain >= 0 and saving >= 0.15:
        label = "Likely fit"
    else:
        label = "Weak fit"
        reasons.append("Grep and Read did as well on these questions. Try a larger project.")
    return {
        "label": label,
        "token_saving": round(saving, 3),
        "hit3_gain": round(gain, 3),
        "reasons": reasons,
    }


def run_quick(
    root: Path,
    count: int = 30,
    seed: int = 7,
    keep_index: bool = False,
    progress=None,
) -> Dict[str, Any]:
    """Run the quick tier on ``root``. Returns the result record (schema v1 body)."""
    import nexus_mcp.core_api as core_api

    root = Path(root).resolve()
    say = progress or (lambda msg: None)
    tmp: Optional[tempfile.TemporaryDirectory] = None
    if keep_index:
        storage = root / ".nexus"
    else:
        tmp = tempfile.TemporaryDirectory(prefix="nexus-eval-")
        storage = Path(tmp.name)
    try:
        with isolated_nexus(storage):
            say("Indexing the project (the first run downloads the embedding model once)...")
            started = time.time()
            index_result = asyncio.run(core_api.index(str(root)))
            if index_result.get("error"):
                raise RuntimeError(index_result["error"])
            index_seconds = round(time.time() - started, 1)
            if not index_result.get("total_chunks"):
                raise RuntimeError(
                    "No source code was indexed. Point eval at a folder with code in a "
                    "supported language."
                )
            rows = read_symbol_rows(storage)
            qs = questions.generate(rows, root, count=count, seed=seed)
            if not qs:
                raise RuntimeError(
                    "No usable questions: the index has no named functions, classes or methods."
                )
            say(f"Asking {len(qs)} questions of both conditions...")
            results = [run_one(root, q, lambda text: core_api.search(text, limit=10)) for q in qs]
    finally:
        if tmp is not None:
            tmp.cleanup()

    files = index_result.get("total_files", 0)
    summary = {"grep": _summarise(results, "grep"), "nexus": _summarise(results, "nexus")}
    by_kind = {}
    for kind in questions.KINDS:
        subset = [r for r in results if r["kind"] == kind]
        if subset:
            by_kind[kind] = {
                "grep": _summarise(subset, "grep"),
                "nexus": _summarise(subset, "nexus"),
            }
    languages = sorted({r["language"] for r in rows if r.get("language")})
    return {
        "tier": "quick",
        "repo": {
            "files": files,
            "chunks": index_result.get("total_chunks", 0),
            "size_band": size_band(files),
            "languages": languages,
            "index_seconds": index_seconds,
        },
        "questions": len(qs),
        "seed": seed,
        "summary": summary,
        "by_kind": by_kind,
        "verdict": verdict(summary, files),
        "results": results,
    }
