"""Impact eval: does `nexus-mcp-ci check` name what a change breaks?

    python -m evals.impact.run                 # a table
    python -m evals.impact.run --json          # the numbers, for CHANGELOG.md
    python -m evals.impact.run --min-recall 0.8   # exit 1 when recall is lower

Each case in `cases.yaml` is a tiny project, one change and the places that the change
breaks. The runner makes a git repository, saves the graph of the base commit as the
stored graph of an index, applies the change and runs the check. No LLM, no model and no
network. Recall is the share of the expected places that the check names: a caller by its
key, or a text-only use by its file. Precision is the share of the claims (callers and
text references) that are correct. A text reference to a file that holds an expected
break is correct, but it does not count toward recall of a break that is expected by key.

A case that the check misses stays in the file. Do not edit a case until it passes.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Set

import yaml

from nexus_mcp.core.graph_models import UniversalGraph
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from nexus_mcp.evaluate import check
from nexus_mcp.indexing.call_resolver import resolve_calls
from nexus_mcp.indexing.pipeline import _transfer_graph
from nexus_mcp.parsing.astgrep_parser import AstGrepParser
from nexus_mcp.persistence.store import GraphPersistence

CASES = Path(__file__).with_name("cases.yaml")
_GIT = ["git", "-c", "user.name=impact", "-c", "user.email=impact@example.com"]
# What counts as a claim that something was left behind.
CLAIM_KINDS = ("untouched_caller", "caller_lines_unchanged", "reference_untouched")


def _git(repo: Path, *args: str) -> None:
    subprocess.run([*_GIT, "-C", str(repo), *args], capture_output=True, check=True)


def _write(repo: Path, files: Dict[str, str]) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _save_graph(repo: Path) -> Path:
    universal, parser = UniversalGraph(), AstGrepParser()
    for path in sorted(repo.rglob("*")):
        if ".git" in path.parts or ".nexus" in path.parts or not path.is_file():
            continue
        if parser.can_parse(str(path)):
            parser.parse_file(str(path), universal)
    graph = RustworkxCodeGraph()
    _transfer_graph(universal, graph)
    resolve_calls(graph)
    store = repo / ".nexus"
    store.mkdir(exist_ok=True)
    GraphPersistence(str(store / "graph.db")).save(graph)
    return store


def run_case(case: Dict[str, Any], workdir: Path) -> Dict[str, Any]:
    """Run one case and return its numbers and what was missed."""
    repo = (workdir / case["id"]).resolve()
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _write(repo, case["files"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    store = _save_graph(repo)
    _write(repo, case["change"])
    report = check.run_check(repo, store, base="HEAD")

    expected_keys: Set[str] = set(case.get("breaks", []))
    expected_files: Set[str] = set(case.get("breaks_files", []))
    unseen: Set[str] = set(case.get("breaks_unseen", []))
    expected_tests: Set[str] = set(case.get("tests", []))

    named_keys = {f.caller for f in report.findings if f.kind in CLAIM_KINDS[:2]}
    named_files = {f.file for f in report.findings if f.kind == "reference_untouched"}
    found = (expected_keys & named_keys) | (expected_files & named_files)
    expected = len(expected_keys) + len(expected_files) + len(unseen)
    # A text reference to a file that holds an expected break is a correct claim, even
    # when the break is expected by key. It does not count as found: it names the file,
    # not the function.
    host_files = expected_files | {k.split("::")[0] for k in expected_keys | unseen}
    correct = len(expected_keys & named_keys) + len(named_files & host_files)
    claims = len(named_keys) + len(named_files)
    tests_found = expected_tests & set(report.affected_tests)
    return {
        "id": case["id"],
        "language": case["language"],
        "expected": expected,
        "found": len(found),
        "claims": claims,
        "correct": correct,
        "recall": len(found) / expected if expected else 1.0,
        "precision": correct / claims if claims else 1.0,
        "tests_expected": len(expected_tests),
        "tests_found": len(tests_found),
        "missed": sorted((expected_keys - named_keys) | (expected_files - named_files) | unseen),
        "extra": sorted((named_keys - expected_keys) | (named_files - host_files)),
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pooled recall and precision over all cases, and over each language."""

    def pool(subset: List[Dict[str, Any]]) -> Dict[str, Any]:
        expected = sum(r["expected"] for r in subset)
        found = sum(r["found"] for r in subset)
        claims = sum(r["claims"] for r in subset)
        correct = sum(r["correct"] for r in subset)
        tests = sum(r["tests_expected"] for r in subset)
        return {
            "cases": len(subset),
            "recall": round(found / expected, 3) if expected else 1.0,
            "precision": round(correct / claims, 3) if claims else 1.0,
            "tests_recall": round(sum(r["tests_found"] for r in subset) / tests, 3)
            if tests else 1.0,
        }

    out = {"all": pool(rows)}
    for language in sorted({r["language"] for r in rows}):
        out[language] = pool([r for r in rows if r["language"] == language])
    return out


def run_all(cases_path: Path = CASES) -> Dict[str, Any]:
    cases = yaml.safe_load(cases_path.read_text())["cases"]
    with tempfile.TemporaryDirectory(prefix="nexus-impact-") as tmp:
        rows = [run_case(case, Path(tmp)) for case in cases]
    return {"cases": rows, "summary": summarize(rows)}


def render(result: Dict[str, Any]) -> str:
    lines = [
        f"{'case':34s} {'recall':>7s} {'precision':>9s} {'tests':>6s}  missed / extra",
    ]
    for r in result["cases"]:
        tests = f"{r['tests_found']}/{r['tests_expected']}"
        notes = []
        if r["missed"]:
            notes.append("missed " + ", ".join(r["missed"]))
        if r["extra"]:
            notes.append("extra " + ", ".join(r["extra"]))
        lines.append(
            f"{r['id']:34s} {r['recall']:7.2f} {r['precision']:9.2f} {tests:>6s}  "
            + "; ".join(notes)
        )
    lines.append("")
    for name, s in result["summary"].items():
        lines.append(
            f"{name:11s} cases={s['cases']:2d}  recall={s['recall']:.3f}  "
            f"precision={s['precision']:.3f}  tests_recall={s['tests_recall']:.3f}"
        )
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--json", action="store_true", help="Print the numbers as JSON")
    p.add_argument("--cases", type=Path, default=CASES)
    p.add_argument("--min-recall", type=float, default=None)
    args = p.parse_args(argv)
    result = run_all(args.cases)
    print(json.dumps(result, indent=2) if args.json else render(result))
    if args.min_recall is not None and result["summary"]["all"]["recall"] < args.min_recall:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
