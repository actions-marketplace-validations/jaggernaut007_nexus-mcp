"""The agent tier: a real A/B run of Claude Code or Codex, with and without Nexus-MCP.

Each task asks the agent which file implements a described behaviour of your code. The
right file comes from the parser, never from nexus ranking. The index is built once and
every model and condition shares it. The tier spends money and sends code to the model
provider, so the CLI shows an estimate and asks before it starts.
"""

import asyncio
import shutil
import statistics
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from nexus_mcp.evaluate import questions, runners, specs
from nexus_mcp.evaluate.quick import isolated_nexus, read_symbol_rows

TASK_KINDS = ("concept", "described")  # lookup is too easy to need an agent
FAIL_FAST_AFTER = 2  # failed runs, with no success yet, before the rest are skipped


def make_prompt(query: str) -> str:
    return (
        "You are in a code repository. Find the file that defines the code described here:\n"
        f"  {query}\n"
        "Reply with the file path relative to the repository root on the first line, then "
        "one sentence on what the code does. Do not edit anything."
    )


def pick_tasks(rows: Sequence[Dict[str, Any]], root: Path, count: int, seed: int):
    pool = questions.generate(rows, root, count=count * 3, seed=seed)
    tasks = [q for q in pool if q.kind in TASK_KINDS]
    return tasks[:count]


def answer_correct(answer: str, relevant: str) -> bool:
    """True when the answer names the right file (relative or absolute path)."""
    text = (answer or "").replace("\\", "/")
    return relevant.replace("\\", "/") in text


def plan_runs(specs_: Sequence[specs.Spec], conditions: Sequence[str], tasks) -> List[Dict]:
    return [
        {"spec": s, "condition": c, "task": t} for s in specs_ for c in conditions for t in tasks
    ]


def estimate_usd(plan: Sequence[Dict]) -> float:
    return round(sum(specs.run_estimate(item["spec"]) for item in plan), 2)


def build_index(root: Path, storage: Path) -> Dict[str, Any]:
    """Index ``root`` into ``storage`` and return the symbol rows for task generation."""
    import nexus_mcp.core_api as core_api

    with isolated_nexus(storage):
        result = asyncio.run(core_api.index(str(root)))
        if result.get("error"):
            raise RuntimeError(result["error"])
        if not result.get("total_chunks"):
            raise RuntimeError("No source code was indexed.")
        return {"rows": read_symbol_rows(storage), "files": result.get("total_files", 0)}


def _median(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return round(statistics.median(clean), 4) if clean else None


def summarise(runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per model and condition: accuracy, median tokens, cost, turns and time."""
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in runs:
        groups.setdefault((r["spec"], r["condition"]), []).append(r)
    rows = []
    for (label, condition), items in groups.items():
        done = [r for r in items if not r.get("skipped")]
        rows.append(
            {
                "spec": label,
                "condition": condition,
                "runs": len(done),
                "errors": sum(1 for r in done if r.get("is_error")),
                "accuracy": round(sum(r["correct"] for r in done) / len(done), 3) if done else None,
                "median_tokens": _median([r.get("tokens") for r in done]),
                "median_cost_usd": _median([r.get("cost_usd") for r in done]),
                "median_turns": _median([r.get("turns") for r in done]),
                "median_seconds": _median([r.get("seconds") for r in done]),
            }
        )
    return rows


def recommend(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The cheapest model and setup whose accuracy is not below its own model's baseline."""
    baseline = {r["spec"]: r["accuracy"] for r in rows if r["condition"] == "baseline"}
    fits = [
        r
        for r in rows
        if r["accuracy"] is not None
        and r["median_cost_usd"] is not None
        and r.get("errors", 0) < r.get("runs", 1)  # a setup whose runs all failed is no answer
        and r["accuracy"] >= baseline.get(r["spec"], 0) - 1e-9
    ]
    if not fits:
        return {
            "spec": None,
            "condition": None,
            "reason": "No setup finished a run without an error.",
        }
    best = min(fits, key=lambda r: (r["median_cost_usd"], -r["accuracy"]))
    base = next(
        (r for r in rows if r["spec"] == best["spec"] and r["condition"] == "baseline"), None
    )
    reason = f"Lowest median cost (${best['median_cost_usd']:.3f} per task) at accuracy "
    reason += f"{best['accuracy']:.0%}"
    if base and base is not best and base["median_cost_usd"]:
        saving = 1 - best["median_cost_usd"] / base["median_cost_usd"]
        reason += f", {saving:.0%} cheaper than Grep and Read on the same model"
    return {"spec": best["spec"], "condition": best["condition"], "reason": reason + "."}


def run_agent(
    root: Path,
    specs_: Sequence[specs.Spec],
    conditions: Sequence[str],
    tasks: int = 4,
    seed: int = 7,
    max_usd: float = 5.0,
    concurrency: int = 3,
    confirm: Callable[[Dict[str, Any]], bool] = lambda plan: True,
    progress: Callable[[str], None] = lambda msg: None,
    run_fn=runners.run,
) -> Dict[str, Any]:
    """Run the agent tier and return the result record (schema v1 body)."""
    root = Path(root).resolve()
    tmp = tempfile.mkdtemp(prefix="nexus-agent-index-")
    storage = Path(tmp)
    try:
        progress("Indexing the project once for every model and condition...")
        built = build_index(root, storage)
        chosen = pick_tasks(built["rows"], root, tasks, seed)
        if not chosen:
            raise RuntimeError("No usable tasks: the index has too few described symbols.")
        plan = plan_runs(specs_, conditions, chosen)
        estimate = estimate_usd(plan)
        if not confirm({"runs": len(plan), "estimate_usd": estimate, "max_usd": max_usd}):
            raise RuntimeError("Cancelled. Nothing was sent.")

        spent = [0.0]
        tally = {"ok": 0, "failed": 0, "first_error": ""}
        lock = threading.Lock()

        def one(item: Dict[str, Any]) -> Dict[str, Any]:
            spec, condition, task = item["spec"], item["condition"], item["task"]
            base = {"spec": spec.label, "condition": condition, "task": task.id}
            with lock:
                if spent[0] >= max_usd:
                    return {**base, "skipped": "spend cap", "correct": False}
                # Stop early when the first runs all fail: a login or CLI problem would
                # otherwise repeat for every run.
                if tally["ok"] == 0 and tally["failed"] >= FAIL_FAST_AFTER:
                    return {**base, "skipped": "earlier runs failed", "correct": False}
            result = run_fn(spec, condition, make_prompt(task.query), root, storage)
            cost = result.get("cost_usd")
            if cost is None:
                # No reported cost: count the estimate, unless the run failed before any call.
                cost = 0.0 if result.get("is_error") else specs.run_estimate(spec)
            with lock:
                spent[0] += cost
                if result.get("is_error"):
                    tally["failed"] += 1
                    tally["first_error"] = tally["first_error"] or result.get("error") or "error"
                else:
                    tally["ok"] += 1
            status = (
                f"error: {result.get('error') or 'unknown'}" if result.get("is_error") else "done"
            )
            progress(f"  {spec.label} {condition} {task.id}: {status}")
            correct = answer_correct(result.get("answer", ""), task.relevant)
            return {**base, **result, "correct": correct}

        progress(f"Running {len(plan)} agent runs, {concurrency} at a time...")
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            runs = list(pool.map(one, plan))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    rows = summarise(runs)
    return {
        "tier": "agent",
        "repo": {"files": built["files"]},
        "tasks": [{"id": t.id, "kind": t.kind} for t in chosen],
        "specs": [s.label for s in specs_],
        "conditions": list(conditions),
        "estimate_usd": estimate,
        "spent_usd": round(spent[0], 4),
        "max_usd": max_usd,
        "skipped": sum(1 for r in runs if r.get("skipped")),
        "failed": tally["failed"],
        "error": tally["first_error"] if tally["ok"] == 0 else None,
        "isolation": sorted({r.get("isolation", "") for r in runs if r.get("isolation")}),
        "summary": rows,
        "recommendation": recommend(rows),
        "runs": [{k: v for k, v in r.items() if k != "answer"} for r in runs],
    }
