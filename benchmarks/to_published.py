"""Copy per-language results from run summaries into benchmarks/published/results.json.

Only the languages you name are written. The others are removed from the published list,
so a language that does not win is not shown.

Usage:
    python -m benchmarks.to_published --before before --after final \
        --languages TypeScript,Java,C++ [--after-version "2.4.0 pre-release (11cfccc)"] \
        [--after-for TypeScript=v2.4.0]

A language with no live run in `--before` gets its after run as its only published run.

Then run `python scripts/publish_results.py` to rewrite the README, the site and llms.txt.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "benchmarks" / "results"
PUBLISHED = ROOT / "benchmarks" / "published" / "results.json"
RETRIEVAL_MODE = "hybrid-source-first"  # the mode of the summary table (summarize_languages)


def load_rows(label: str) -> Dict[str, Any]:
    """The summary rows of one run, keyed by corpus name."""
    return json.loads((RESULTS_DIR / f"languages-{label}.json").read_text())["rows"]


def _ok(tier: Any) -> bool:
    """True when a tier ran and holds results (not a crash or a skip note)."""
    return isinstance(tier, dict) and not tier.get("status") and not tier.get("error")


def run_entry(row: Dict[str, Any], version: str) -> Dict[str, Any]:
    """The published `before` or `after` block of one corpus row."""
    out: Dict[str, Any] = {"nexus_version": version}
    q = row.get("quick")
    if _ok(q) and "summary" in q:
        s = q["summary"]
        out["date"] = q["created"][:10]
        out["index_seconds"] = q["repo"]["index_seconds"]
        out["quick_tier"] = {
            side: {"hit_at_1": s[side]["hit_at_1"], "median_tokens": s[side]["median_tokens"]}
            for side in ("grep", "nexus")
        }
        out["quick_tier"]["verdict"] = q["verdict"]["label"]
    r = row.get("retrieval")
    if _ok(r) and "modes" in r:
        out["retrieval_hit_at_1"] = round(r["modes"][RETRIEVAL_MODE]["hit@1"], 2)
    g = row.get("graph")
    if _ok(g):
        fns = sum(v["functions"] for v in g.values())
        calls = sum(v["calls"] for v in g.values())
        out["graph"] = {"fn_nodes": fns, "calls_per_fn": round(calls / fns, 2) if fns else 0}
    live = row.get("live")
    if _ok(live) and "conditions" in live:
        c = live["conditions"]
        if {"baseline", "nexus"} <= set(c):
            out["live"] = {
                side: {
                    "median_tokens": round(c[side]["total_tokens"]),
                    "cost_usd": round(c[side]["total_cost_usd"], 3),
                    "correct": c[side]["tasks_correct"],
                    "of": c[side]["n_tasks"],
                }
                for side in ("baseline", "nexus")
            }
    return out


def language_entry(
    name: str,
    before: Dict[str, Any],
    after: Optional[Dict[str, Any]],
    before_version: str,
    after_version: str,
) -> Dict[str, Any]:
    """One published language entry from the rows of one corpus in two runs."""
    base = after or before
    live = base.get("live") if _ok(base.get("live")) else before.get("live")
    q = base.get("quick") if _ok(base.get("quick")) else before.get("quick")
    entry: Dict[str, Any] = {
        "language": base["language"],
        "project": base["repo"],
        "url": base["url"].removesuffix(".git"),
        "pin": base["pin"],
        "files": q["repo"]["files"] if _ok(q) else None,
        "model": (live or {}).get("model", "sonnet"),
        "effort": (live or {}).get("effort", "medium"),
        "isolation": ", ".join((live or {}).get("isolation", [])) or None,
        "before": run_entry(before, before_version),
    }
    if after:
        entry["after"] = run_entry(after, after_version)
    return entry


def label_version(label: str, default: str) -> str:
    """The Nexus-MCP version of a run label: "v2.4.0" is "2.4.0", else the default."""
    return label[1:] if label[:2] in {f"v{d}" for d in "0123456789"} else default


def build(languages: List[str], before: str, after: Optional[str],
          before_version: str, after_version: str,
          after_for: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Published entries for each named language, in the order given.

    `after_for` maps a language to the label of its own "after" run, in place of `after`.
    """
    after_for = after_for or {}
    b_rows = load_rows(before)
    out = []
    for lang in languages:
        a_label = after_for.get(lang, after)
        a_rows = load_rows(a_label) if a_label else {}
        a_version = label_version(a_label, after_version) if a_label in after_for.values() \
            else after_version
        names = [n for n, r in b_rows.items() if r["language"] == lang and _ok(r.get("live"))]
        if not names:
            names = [n for n, r in a_rows.items() if r["language"] == lang and _ok(r.get("live"))]
        if not names:
            sys.exit(f"No corpus with a live run for {lang}")
        name = names[0]
        before_row, after_row = b_rows.get(name), a_rows.get(name)
        if after_row and not _ok(after_row.get("live")):
            # An after run without the live tier would show "x → –" in the live columns.
            # Publish the complete before run only.
            after_row = None
        if before_row is None or not _ok(before_row.get("live")):
            # No before run with the live tier: publish the after run as the only run.
            out.append(language_entry(name, after_row, None, a_version, ""))
            continue
        out.append(language_entry(name, before_row, after_row, before_version, a_version))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--before", required=True)
    p.add_argument("--after")
    p.add_argument("--languages", required=True, help="Comma-separated, in display order")
    p.add_argument("--before-version", default="2.2.2")
    p.add_argument("--after-version", default="")
    p.add_argument("--after-for", action="append", default=[], metavar="LANGUAGE=LABEL",
                   help="Use another after run for one language, such as TypeScript=v2.4.0")
    args = p.parse_args(argv)
    after_for = dict(s.split("=", 1) for s in args.after_for)
    langs = build([s.strip() for s in args.languages.split(",")], args.before, args.after,
                  args.before_version, args.after_version or args.after or "", after_for)
    data = json.loads(PUBLISHED.read_text())
    data["languages"] = langs
    PUBLISHED.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {len(langs)} languages to {PUBLISHED.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
