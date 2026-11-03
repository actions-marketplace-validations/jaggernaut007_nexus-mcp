"""Write every published benchmark number from one file: benchmarks/published/results.json.

Run: python scripts/publish_results.py          (rewrite the generated blocks and files)
     python scripts/publish_results.py --check  (exit 1 when a generated block is out of date)

Generated blocks sit between `<!-- results:start -->` and `<!-- results:end -->` in README.md,
site/index.html and llms.txt. Do not edit them by hand. The script also writes
site/benchmarks.json, site/benchmarks.csv and site/benchmarks.html.
"""

import argparse
import csv
import html
import io
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "benchmarks" / "published" / "results.json"
START, END = "<!-- results:start -->", "<!-- results:end -->"
SITE_URL = "https://jaggernaut007.github.io/nexus-mcp"
REPO_URL = "https://github.com/jaggernaut007/Nexus-MCP"


def pct_change(new: float, old: float) -> str:
    change = (new - old) / old * 100 if old else 0.0
    sign = "−" if change < 0 else "+"
    return f"{sign}{abs(change):.0f}%"


def ascii_pct(new: float, old: float) -> str:
    return pct_change(new, old).replace("−", "-")


def total_correct(data: Dict, side: str) -> str:
    runs = data["runs"]
    return f"{sum(r[side]['correct'] for r in runs)} of {sum(r[side]['of'] for r in runs)}"


def readme_block(data: Dict) -> str:
    m = data["method"]
    lines = [
        "| Project | Cost: Grep and Read → Nexus-MCP | Turns | Correct answers |",
        "|---|---:|---:|---:|",
    ]
    for r in data["runs"]:
        b, n = r["baseline"], r["nexus"]
        name = f"[{r['project']}]({r['url']})" if r.get("url") else r["project"]
        lines.append(
            f"| {name} ({r['files']:,} files) | ${b['cost_usd']:.3f} → ${n['cost_usd']:.3f} "
            f"(**{ascii_pct(n['cost_usd'], b['cost_usd'])}**) | {b['turns']} → {n['turns']} | "
            f"{b['correct']} → {n['correct']} of {n['of']} |"
        )
    q = data["quick_tier"][0]
    ret = data["retrieval"]
    suites = ", ".join(f"`{k}` {v:.2f}" for k, v in ret["suites"].items())
    lines += [
        "",
        f"Live runs: {m['agent']}, {m['questions_per_project']} questions per project, "
        f"{m['runs_per_question']} runs each, version {data['version']} ({data['updated']}). "
        f"{m['noise']} Search {ret['metric']} on the four retrieval suites: {suites}.",
        f"`nexus-mcp-ci eval --example` (the {q['project']}, {q['files']} files, no LLM): "
        f"{q['grep']['median_tokens']:,} → {q['nexus']['median_tokens']:,} median tokens to "
        f"reach the right file, right file first {q['grep']['hit_at_1']:.0%} → "
        f"{q['nexus']['hit_at_1']:.0%}.",
        f"All numbers: [benchmarks page]({SITE_URL}/benchmarks.html) · "
        f"[JSON]({SITE_URL}/benchmarks.json) · [CSV]({SITE_URL}/benchmarks.csv).",
    ]
    return "\n".join(lines)


def site_block(data: Dict) -> str:
    rows: List[str] = []
    for r in data["runs"]:
        b, n = r["baseline"], r["nexus"]
        label = f"{html.escape(r['project'])}, {r['files']:,} files"
        change = pct_change(n["cost_usd"], b["cost_usd"])
        muted = "" if (n["cost_usd"] - b["cost_usd"]) / b["cost_usd"] <= -0.10 else " muted"
        rows.append(
            f'<tr><td>{label}: turns</td><td><span class="pill">'
            f"{b['turns']} → {n['turns']}</span></td></tr>"
        )
        rows.append(
            f'<tr><td>{html.escape(r["project"])}: cost</td><td><span class="pill{muted}">'
            f"{change}</span></td></tr>"
        )
    rows.append(
        '<tr><td>Correct answers: Nexus, then Grep and Read</td><td><span class="pill muted">'
        f"{total_correct(data, 'nexus').split(' of ')[0]} · {total_correct(data, 'baseline')}"
        "</span></td></tr>"
    )
    return "\n".join("            " + row for row in rows)


def llms_block(data: Dict) -> str:
    lines = [
        "## Benchmarks",
        "",
        f"Source of truth: {SITE_URL}/benchmarks.json (updated {data['updated']}, "
        f"version {data['version']}). Method: {data['method']['agent']}, "
        f"{data['method']['runs_per_question']} runs per question.",
        "",
    ]
    for r in data["runs"]:
        b, n = r["baseline"], r["nexus"]
        lines.append(
            f"- {r['project']} ({r['files']} files): cost ${b['cost_usd']:.3f} with Grep and "
            f"Read, ${n['cost_usd']:.3f} with Nexus-MCP ({ascii_pct(n['cost_usd'], b['cost_usd'])}); "
            f"turns {b['turns']} to {n['turns']}; correct {b['correct']} and {n['correct']} "
            f"of {n['of']}."
        )
    q = data["quick_tier"][0]
    lines.append(
        f"- Quick tier, {q['project']}: median tokens to the right file "
        f"{q['grep']['median_tokens']} (Grep and Read) and {q['nexus']['median_tokens']} "
        f"(Nexus-MCP)."
    )
    lines.append(f"- {data['method']['noise']}")
    return "\n".join(lines)


def csv_text(data: Dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        ["project", "files", "commit", "date", "condition", "cost_usd", "turns", "correct", "of"]
    )
    for r in data["runs"]:
        for side, name in (("baseline", "grep_and_read"), ("nexus", "nexus_mcp")):
            s = r[side]
            writer.writerow(
                [
                    r["project"],
                    r["files"],
                    r["commit"],
                    r["date"],
                    name,
                    s["cost_usd"],
                    s["turns"],
                    s["correct"],
                    s["of"],
                ]
            )
    return out.getvalue()


def page_html(data: Dict) -> str:
    m = data["method"]
    rows = []
    for r in data["runs"]:
        b, n = r["baseline"], r["nexus"]
        name = html.escape(r["project"])
        if r.get("url"):
            name = f'<a href="{html.escape(r["url"])}">{name}</a>'
        rows.append(
            f"<tr><td>{name}</td><td>{r['files']:,}</td><td>${b['cost_usd']:.3f}</td>"
            f"<td>${n['cost_usd']:.3f}</td><td>{pct_change(n['cost_usd'], b['cost_usd'])}</td>"
            f"<td>{b['turns']} → {n['turns']}</td><td>{b['correct']} / {n['correct']} of "
            f"{n['of']}</td><td><code>{r['commit']}</code>, {r['date']}</td></tr>"
        )
    suites = "".join(
        f"<tr><td><code>{html.escape(k)}</code></td><td>{v:.2f}</td></tr>"
        for k, v in data["retrieval"]["suites"].items()
    )
    q = data["quick_tier"][0]
    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Nexus-MCP token-efficiency benchmark",
        "description": "Live agent runs of Claude Code with Nexus-MCP against Grep and Read: "
        "cost, turns and correct answers per project.",
        "url": f"{SITE_URL}/benchmarks.html",
        "dateModified": data["updated"],
        "license": f"{REPO_URL}/blob/main/LICENSE",
        "creator": {"@type": "Person", "name": "Shreyas Jagannath"},
        "distribution": [
            {
                "@type": "DataDownload",
                "encodingFormat": "application/json",
                "contentUrl": f"{SITE_URL}/benchmarks.json",
            },
            {
                "@type": "DataDownload",
                "encodingFormat": "text/csv",
                "contentUrl": f"{SITE_URL}/benchmarks.csv",
            },
        ],
    }
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nexus-MCP Benchmarks</title>
<meta name="description" content="Measured cost, turns and accuracy of Claude Code with the Nexus-MCP code-intelligence server against Grep and Read, with method and raw data.">
<link rel="canonical" href="{SITE_URL}/benchmarks.html">
<script type="application/ld+json">{json.dumps(dataset)}</script>
<style>
:root {{ --bg:#fbfaf7; --fg:#1d1b18; --muted:#5f5a52; --line:#e3ded4; --accent:#0f6b5c; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#14130f; --fg:#ece8df; --muted:#a39d91; --line:#2e2b25; --accent:#5cc7b2; }} }}
:root[data-theme="dark"] {{ --bg:#14130f; --fg:#ece8df; --muted:#a39d91; --line:#2e2b25; --accent:#5cc7b2; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.6 system-ui, sans-serif; }}
main {{ max-width:960px; margin:0 auto; padding:32px 16px 64px; }}
h1 {{ font-size:2rem; margin:0 0 8px; }} h2 {{ margin-top:40px; }}
p, li {{ color:var(--muted); }} a {{ color:var(--accent); }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }}
th {{ font-size:.85rem; color:var(--muted); font-weight:600; }}
code {{ font-size:.9em; }}
</style>
</head>
<body>
<main>
<p><a href="./">← Nexus-MCP</a></p>
<h1>Benchmarks</h1>
<p>Nexus-MCP is a local code-intelligence MCP server. These are live runs of {html.escape(m["agent"])} with Nexus-MCP against an agent with only Grep, Glob and Read. Updated {data["updated"]}, version {data["version"]}.</p>
<h2>Live agent runs</h2>
<div class="scroll"><table>
<thead><tr><th>Project</th><th>Files</th><th>Cost: Grep and Read</th><th>Cost: Nexus-MCP</th><th>Change</th><th>Turns</th><th>Correct (Grep / Nexus)</th><th>Commit, date</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody></table></div>
<p>{m["questions_per_project"]} questions per project, {m["runs_per_question"]} runs each. {html.escape(m["noise"])} The saving is large on code the model does not know and small on code it knows from training.</p>
<h2>Search accuracy (no LLM)</h2>
<div class="scroll"><table><thead><tr><th>Retrieval suite</th><th>{html.escape(data["retrieval"]["metric"])}</th></tr></thead><tbody>{suites}</tbody></table></div>
<h2>Quick tier on the example</h2>
<p><code>nexus-mcp-ci eval --example</code> on the {html.escape(q["project"])} ({q["files"]} files, {q["questions"]} questions, no LLM): median tokens to reach the right file {q["grep"]["median_tokens"]:,} with Grep and Read and {q["nexus"]["median_tokens"]:,} with Nexus-MCP. Right file first: {q["grep"]["hit_at_1"]:.0%} and {q["nexus"]["hit_at_1"]:.0%}. Verdict: {html.escape(q["verdict"])}.</p>
<h2>Run it on your own code</h2>
<p><code>uvx nexus-mcp-ci eval</code> runs the free offline check on the current folder. <code>uvx nexus-mcp-ci eval --agent claude</code> runs the paid A/B with your own Claude Code login, after it shows the cost.</p>
<h2>Data</h2>
<ul>
<li><a href="benchmarks.json">benchmarks.json</a> (source of truth)</li>
<li><a href="benchmarks.csv">benchmarks.csv</a></li>
<li><a href="{html.escape(m["source"])}">Method and run history (docs/EVALS.md)</a></li>
</ul>
</main>
</body>
</html>
"""


def replace_block(text: str, block: str, path: Path) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        raise SystemExit(f"{path}: markers {START} ... {END} not found")
    # Keep the indent of the start marker, so the end marker lines up with it.
    match = pattern.search(text)
    line_start = text.rfind("\n", 0, match.start()) + 1
    indent = text[line_start : match.start()]
    indent = indent if not indent.strip() else ""
    return pattern.sub(lambda _: f"{START}\n{block}\n{indent}{END}", text, count=1)


def outputs(data: Dict) -> Dict[Path, str]:
    files = {
        ROOT / "site" / "benchmarks.json": json.dumps(data, indent=2) + "\n",
        ROOT / "site" / "benchmarks.csv": csv_text(data),
        ROOT / "site" / "benchmarks.html": page_html(data),
    }
    for rel, block in (
        ("README.md", readme_block(data)),
        ("site/index.html", site_block(data)),
        ("llms.txt", llms_block(data)),
    ):
        path = ROOT / rel
        files[path] = replace_block(path.read_text(), block, path)
    return files


def _show(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Fail when output is out of date")
    args = parser.parse_args(argv)
    data = json.loads(DATA.read_text())
    stale = []
    for path, text in outputs(data).items():
        current = path.read_text() if path.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(_show(path))
        else:
            path.write_text(text)
            print(f"wrote {_show(path)}")
    if stale:
        print("Out of date (run python scripts/publish_results.py):", *stale, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
