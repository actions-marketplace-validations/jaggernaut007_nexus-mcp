"""Write the latest result of each benchmark repo into benchmarks/published/results.json.

The raw runs are in benchmarks/runs/<label>/. A repo can have many runs. This script keeps
only the latest run of each tier (the offline check, retrieval, graph, the live A/B), so the
README and the site show one row per repo. A repo with only old runs keeps its old run. Each
row names the build of its run.

The Grep and Read side of the live A/B does not depend on the Nexus-MCP build. So the latest
Nexus-MCP side is paired with the latest Grep and Read side, even when they come from two runs.
scripts/publish_results.py used the same rule before. The two sides must have the same model,
effort and number of tasks; else the build stops with an error.

Usage:
    python -m benchmarks.latest_published          # rewrite the "repos" list
    python -m benchmarks.latest_published --check  # exit 1 when the list is out of date

Then run `python scripts/publish_results.py` to rewrite the README, the site and llms.txt.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks.corpora import graph_totals
from benchmarks.report import aggregate_by_condition

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "benchmarks" / "runs"
PUBLISHED = ROOT / "benchmarks" / "published" / "results.json"
RETRIEVAL_MODE = "hybrid-source-first"  # the mode of the summary table (summarize_languages)

# The runs, oldest first. The order comes from the commit of each run (benchmarks/runs/README.md).
ORDER = [
    "languages-before",
    "languages-after",
    "live-opt2",
    "languages-final",
    "languages-v2.3.0",
    "languages-v2.4.0",
    "languages-py-fix",
    "languages-refs-fix",
    "languages-defs-fix",
    "languages-graph-cap",
    "languages-v2.4.1",
]
# Run folders of a tool comparison ("comparisons" in results.json), never of the repo table.
COMPARISON_RUN_PREFIX = "competitors-"
# Display order: one row per repo.
REPOS = [
    "home-assistant", "django", "flask", "vite", "webpack", "commons-lang",
    "newtonsoft-json", "laravel", "chromium-base", "prometheus", "redis", "tokio",
]  # fmt: skip


def _ok(tier: Any) -> bool:
    """True when a tier ran and holds results (not a crash or a skip note)."""
    return isinstance(tier, dict) and not tier.get("status") and not tier.get("error")


def build_label(row: Dict[str, Any], label: str) -> str:
    """The build of a run: "2.4.1" for a release run, else "2.2.2+11cfccc"."""
    version = row.get("nexus_version") or "?"
    if label.startswith("languages-v"):
        return version
    ref = (row.get("nexus_ref") or "")[:7]
    return f"{version}+{ref}" if ref else version


def load_runs(runs_dir: Path = RUNS_DIR) -> List[Dict[str, Dict[str, Any]]]:
    """The summary rows of each run in ORDER, keyed by repo. A missing run gives {}."""
    out = []
    for label in ORDER:
        folder = runs_dir / label
        summary = folder / f"{label}.json"
        if summary.exists():
            rows = json.loads(summary.read_text())["rows"]
        elif folder.is_dir():
            rows = live_only_rows(folder)
        else:
            rows = {}
        out.append({name: dict(row, _label=label) for name, row in rows.items()})
    return out


def live_only_rows(folder: Path) -> Dict[str, Dict[str, Any]]:
    """Rows of a run that has only per-task records (<repo>.jsonl), such as live-opt2."""
    rows = {}
    for path in sorted(folder.glob("*.jsonl")):
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if not records:
            continue
        first = records[0]
        per_cell: Dict[Any, int] = {}
        for r in records:
            key = (r.get("task_id"), r.get("condition"))
            per_cell[key] = per_cell.get(key, 0) + 1
        rows[path.stem] = {
            "name": path.stem,
            "nexus_version": first.get("nexus_version"),
            "nexus_ref": first.get("nexus_ref"),
            "live": {
                "model": first.get("model"),
                "effort": first.get("effort"),
                "reps": max(per_cell.values()),
                "conditions": aggregate_by_condition(records),
            },
        }
    return rows


def _latest(runs: List[Dict[str, Dict[str, Any]]], name: str, test) -> Optional[Dict[str, Any]]:
    """The row of the latest run of `name` that passes `test`."""
    for rows in reversed(runs):
        row = rows.get(name)
        if row and test(row):
            return row
    return None


def _has_quick(row: Dict[str, Any]) -> bool:
    """True when the row has a usable offline-check summary."""
    return _ok(row.get("quick")) and "summary" in row["quick"]


def _has_side(side: str):
    """A test for rows whose live tier has the condition `side` ("baseline" or "nexus")."""

    def test(row: Dict[str, Any]) -> bool:
        live = row.get("live")
        return _ok(live) and side in (live.get("conditions") or {})

    return test


def check_pair(name: str, nexus: Dict[str, Any], baseline: Dict[str, Any]) -> None:
    """Raise ValueError when two live sides from different runs do not have the same setup.

    The pair is fair only with the same model, effort and number of tasks.
    """
    if nexus is baseline:
        return
    n, b = nexus["live"], baseline["live"]
    for key in ("model", "effort"):
        if n.get(key) != b.get(key):
            raise ValueError(
                f"{name}: the live sides differ in {key} "
                f"({nexus['_label']}: {n.get(key)}, {baseline['_label']}: {b.get(key)})"
            )
    tasks_n = n["conditions"]["nexus"]["n_tasks"]
    tasks_b = b["conditions"]["baseline"]["n_tasks"]
    if tasks_n != tasks_b:
        raise ValueError(f"{name}: the live sides have {tasks_n} and {tasks_b} tasks")


def quick_entry(row: Dict[str, Any]) -> Dict[str, Any]:
    """The published offline-check block of a row."""
    q = row["quick"]
    s = q["summary"]
    return {
        "run": row["_label"],
        "build": build_label(row, row["_label"]),
        "date": q["created"][:10],
        "questions": q.get("questions"),
        "index_seconds": q["repo"]["index_seconds"],
        "verdict": q["verdict"]["label"],
        **{
            side: {
                "hit_at_1": s[side]["hit_at_1"],
                "hit_at_3": s[side].get("hit_at_3"),
                "median_tokens": round(s[side]["median_tokens"]),
                "median_ms": s[side].get("median_ms"),
            }
            for side in ("grep", "nexus")
        },
    }


def live_side(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    """The published block of one live condition (`side`) of a row."""
    c = row["live"]["conditions"][side]
    return {
        "run": row["_label"],
        "build": build_label(row, row["_label"]) if side == "nexus" else "built-in tools",
        "median_tokens": round(c["total_tokens"]),
        "cost_usd": round(c["total_cost_usd"], 4),
        "turns": c.get("num_turns"),
        "wall_seconds": round(c["wall_seconds"], 1) if c.get("wall_seconds") else None,
        "correct": c["tasks_correct"],
        "of": c["n_tasks"],
    }


def repo_entry(name: str, runs: List[Dict[str, Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """The published entry of one repo, from the latest run of each tier."""
    base = _latest(runs, name, lambda r: bool(r.get("repo")))
    if base is None:
        return None
    entry: Dict[str, Any] = {
        "name": name,
        "language": base["language"],
        "project": base["repo"],
        "url": base["url"].removesuffix(".git"),
        "pin": base["pin"],
        "files": None,
    }
    quick = _latest(runs, name, _has_quick)
    if quick:
        entry["files"] = quick["quick"]["repo"]["files"]
        entry["quick"] = quick_entry(quick)
    retrieval = _latest(runs, name, lambda r: _ok(r.get("retrieval")) and "modes" in r["retrieval"])
    if retrieval:
        hit = retrieval["retrieval"]["modes"][RETRIEVAL_MODE]["hit@1"]
        entry["retrieval_hit_at_1"] = round(hit, 2)
    graph = _latest(runs, name, lambda r: _ok(r.get("graph")))
    if graph:
        fns, calls = graph_totals(graph["graph"], base["language"])
        # No function nodes: there is no rate to show, so publish None (a dash), not 0.
        entry["calls_per_fn"] = round(calls / fns, 2) if fns else None
    nexus = _latest(runs, name, _has_side("nexus"))
    baseline = _latest(runs, name, _has_side("baseline"))
    if nexus and baseline:
        check_pair(name, nexus, baseline)
        live = nexus["live"]
        entry["live"] = {
            "model": live.get("model", "sonnet"),
            "effort": live.get("effort", "medium"),
            "reps": live.get("reps", 3),
            "baseline": live_side(baseline, "baseline"),
            "nexus": live_side(nexus, "nexus"),
        }
    return entry


def build(runs_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """The published entry of each repo in REPOS. A repo with no usable run is left out."""
    runs = load_runs(runs_dir or RUNS_DIR)
    return [e for e in (repo_entry(name, runs) for name in REPOS) if e]


def unlisted_runs(runs_dir: Optional[Path] = None) -> List[str]:
    """Run folders in benchmarks/runs/ that ORDER does not list. Such a run is never read.

    `setup` and the `competitors-*` folders are not repo runs: a `competitors-*` folder holds
    the records of a tool comparison, published under "comparisons", not "repos".
    """
    root = runs_dir or RUNS_DIR
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and p.name not in ORDER and not is_non_repo_run(p.name)
    )


def is_non_repo_run(name: str) -> bool:
    """True for a run folder that never feeds the per-repo table."""
    return name == "setup" or name.startswith(COMPARISON_RUN_PREFIX)


def main(argv: Optional[List[str]] = None) -> int:
    """Rewrite "repos" in results.json. With --check, write nothing and return 1 when stale."""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--check", action="store_true", help="Fail when the list is out of date")
    args = p.parse_args(argv)
    data = json.loads(PUBLISHED.read_text())
    missing = unlisted_runs()
    if missing:
        print(f"benchmarks/runs: add these runs to ORDER: {', '.join(missing)}")
        return 1
    repos = build()
    if args.check:
        if data.get("repos") != repos:
            print("benchmarks/published/results.json: repos out of date "
                  "(run python -m benchmarks.latest_published)")
            return 1
        return 0
    data["repos"] = repos
    PUBLISHED.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {len(repos)} repos to {PUBLISHED.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
