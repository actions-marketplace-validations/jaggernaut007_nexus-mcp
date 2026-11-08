"""Write every published benchmark number from one file: benchmarks/published/results.json.

Run: python scripts/publish_results.py          (rewrite the generated blocks and files)
     python scripts/publish_results.py --check  (exit 1 when a generated block is out of date)

Generated blocks sit between `<!-- NAME:start -->` and `<!-- NAME:end -->` markers. README.md
and llms.txt have a `results` block; site/index.html has a `hero` and a `results` block. Do
not edit them by hand. The script also writes site/benchmarks.json, site/benchmarks.csv and
site/benchmarks.html.

Schema 3: "repos" holds one entry per benchmark repo, with the latest run of each tier
(written by `python -m benchmarks.latest_published`).
"""

import argparse
import csv
import html
import io
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "benchmarks" / "published" / "results.json"
COMMUNITY = ROOT / "site" / "data" / "community-results.json"
SCHEMA_VERSIONS = (3,)
SITE_URL = "https://jaggernaut007.github.io/nexus-mcp"
REPO_URL = "https://github.com/jaggernaut007/Nexus-MCP"
# A live result within this share of the baseline counts as "about equal", not a loss.
EQUAL_BAND = 0.05


def markers(name: str):
    return f"<!-- {name}:start -->", f"<!-- {name}:end -->"


START, END = markers("results")


# ---------------------------------------------------------------- numbers


def pct_change(new: float, old: float) -> str:
    change = (new - old) / old * 100 if old else 0.0
    if round(change) == 0:
        return "0%"
    sign = "−" if change < 0 else "+"
    return f"{sign}{abs(change):.0f}%"


def ascii_pct(new: float, old: float) -> str:
    return pct_change(new, old).replace("−", "-")


def _turns(value) -> str:
    return "–" if value is None else f"{value:g}"


def offline_repos(data: Dict) -> List[Dict]:
    return [r for r in data["repos"] if r.get("quick")]


def live_repos(data: Dict) -> List[Dict]:
    return [r for r in data["repos"] if r.get("live")]


def headline(data: Dict) -> Dict:
    """The summary numbers of the hero and the README, computed from the repo rows."""
    off, live = offline_repos(data), live_repos(data)
    cut = [1 - r["quick"]["nexus"]["median_tokens"] / r["quick"]["grep"]["median_tokens"] for r in off]
    speed = [r["quick"]["grep"]["median_ms"] / r["quick"]["nexus"]["median_ms"] for r in off]
    cost = [r["live"]["nexus"]["cost_usd"] / r["live"]["baseline"]["cost_usd"] - 1 for r in live]
    tokens = [r["live"]["nexus"]["median_tokens"] / r["live"]["baseline"]["median_tokens"] - 1 for r in live]
    return {
        "languages": len({r["language"] for r in off}),
        "repos": len(data["repos"]),
        "offline_repos": len(off),
        "token_cut": statistics.median(cut),
        "token_cut_min": min(cut),
        "token_cut_max": max(cut),
        "speedup": statistics.median(speed),
        "speedup_min": min(speed),
        "speedup_max": max(speed),
        "hit_grep": statistics.median(r["quick"]["grep"]["hit_at_1"] for r in off),
        "hit_nexus": statistics.median(r["quick"]["nexus"]["hit_at_1"] for r in off),
        "live_repos": len(live),
        # A change inside the band is "about equal" in the tables, so it is not a win here.
        "cost_lower": sum(1 for c in cost if c < -EQUAL_BAND),
        "cost_median": statistics.median(cost),
        "tokens_lower": sum(1 for t in tokens if t < -EQUAL_BAND),
        "correct_nexus": sum(r["live"]["nexus"]["correct"] for r in live),
        "correct_baseline": sum(r["live"]["baseline"]["correct"] for r in live),
        "tasks": sum(r["live"]["nexus"]["of"] for r in live),
    }


def not_yet(data: Dict) -> List[Dict]:
    """Live repos where Nexus-MCP used clearly more tokens per task than Grep and Read."""
    return [
        r
        for r in live_repos(data)
        if r["live"]["nexus"]["median_tokens"] > r["live"]["baseline"]["median_tokens"] * (1 + EQUAL_BAND)
    ]


def not_yet_sentence(data: Dict) -> str:
    rows = not_yet(data)
    if not rows:
        return ""
    parts = [
        f"{r['language']} ({r['project']}, {ascii_pct(r['live']['nexus']['median_tokens'], r['live']['baseline']['median_tokens'])} tokens, build {r['live']['nexus']['build']})"
        for r in rows
    ]
    return "Not there yet: " + " and ".join(parts) + ". These runs are older; a rerun on the current build is pending."


def summary_sentence(data: Dict) -> str:
    h = headline(data)
    return (
        f"On {h['offline_repos']} open-source repos in {h['languages']} languages, Nexus-MCP "
        f"reached the right file with {h['token_cut']:.0%} fewer tokens (median) and searched "
        f"{h['speedup']:.0f}× faster than Grep and Read. In live Claude Code runs on "
        f"{h['live_repos']} repos, cost per task was lower on {h['cost_lower']} of "
        f"{h['live_repos']}, with {h['correct_nexus']} of {h['tasks']} tasks correct against "
        f"{h['correct_baseline']} of {h['tasks']}."
    )


def _link_md(r: Dict) -> str:
    return f"[{r['project']}]({r['url']})" if r.get("url") else r["project"]


def _better(new: float, old: float, text: str) -> str:
    """Bold a Nexus-MCP value in Markdown when it is lower than the baseline."""
    return f"**{text}**" if new < old else text


# ---------------------------------------------------------------- README


def offline_md(data: Dict) -> List[str]:
    lines = [
        "| Language | Repo | Right file first | Tokens to the right file | Search time |",
        "|---|---|---:|---:|---:|",
    ]
    for r in offline_repos(data):
        g, n = r["quick"]["grep"], r["quick"]["nexus"]
        lines.append(
            f"| {r['language']} | {_link_md(r)} ({r['files']:,} files) | "
            f"{g['hit_at_1']:.0%} → **{n['hit_at_1']:.0%}** | "
            f"{g['median_tokens']:,} → **{n['median_tokens']:,}** | "
            f"{g['median_ms']:,.0f} → **{n['median_ms']:,.0f} ms** |"
        )
    return lines


def live_md(data: Dict) -> List[str]:
    lines = [
        "| Language | Repo | Tokens per task | Cost per task | Turns | Correct |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in live_repos(data):
        b, n = r["live"]["baseline"], r["live"]["nexus"]
        tok = f"{n['median_tokens']:,} ({ascii_pct(n['median_tokens'], b['median_tokens'])})"
        cost = f"${n['cost_usd']:.3f} ({ascii_pct(n['cost_usd'], b['cost_usd'])})"
        lines.append(
            f"| {r['language']} | {_link_md(r)} | {b['median_tokens']:,} → "
            f"{_better(n['median_tokens'], b['median_tokens'] * (1 - EQUAL_BAND), tok)} | "
            f"${b['cost_usd']:.3f} → {_better(n['cost_usd'], b['cost_usd'] * (1 - EQUAL_BAND), cost)} | "
            f"{_turns(b['turns'])} → {_turns(n['turns'])} | {b['correct']} → {n['correct']} of {n['of']} |"
        )
    return lines


def readme_block(data: Dict) -> str:
    m = data["method"]
    lines = [
        f"**{summary_sentence(data)}**",
        "",
        "**Find the right file** (`nexus-mcp-ci eval`: offline, no LLM). Grep and Read → Nexus-MCP:",
        "",
        *offline_md(data),
        "",
        f"**Live agent runs** ({m['agent']}, {m['tasks_per_repo']} tasks per repo, "
        f"{m['runs_per_task']} runs each, median per task). Grep and Read → Nexus-MCP:",
        "",
        *live_md(data),
        "",
    ]
    note = not_yet_sentence(data)
    lines.append(
        (note + " " if note else "")
        + "Each row is the latest run of that repo; the build of each row is on the benchmarks "
        f"page. {m['noise']}"
    )
    lines += [
        "",
        f"All numbers, the method and the raw runs: [benchmarks page]({SITE_URL}/benchmarks.html) · "
        f"[JSON]({SITE_URL}/benchmarks.json) · [CSV]({SITE_URL}/benchmarks.csv) · "
        f"[runs]({m['runs']}).",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- site


def _repo_link(r: Dict) -> str:
    name = html.escape(r["project"]).replace("/", "/<wbr>")
    return f'<a href="{html.escape(r["url"])}" rel="noopener">{name}</a>' if r.get("url") else name


def _cell(new: float, old: float, text: str, equal: float = 0.0) -> str:
    """A Nexus-MCP cell: class "win" when lower, "loss" when clearly higher."""
    if new < old * (1 - equal):
        cls = "win"
    elif new > old * (1 + equal):
        cls = "loss"
    else:
        cls = "even"
    return f'<td class="{cls}">{text}</td>'


def offline_html(data: Dict) -> str:
    rows = []
    for r in offline_repos(data):
        g, n = r["quick"]["grep"], r["quick"]["nexus"]
        rows.append(
            f'<tr><th scope="row">{html.escape(r["language"])}</th>'
            f'<td class="repo">{_repo_link(r)}<span>{r["files"]:,} files</span></td>'
            f'<td class="base">{g["hit_at_1"]:.0%}</td><td class="win">{n["hit_at_1"]:.0%}</td>'
            f'<td class="base">{g["median_tokens"]:,}</td><td class="win">{n["median_tokens"]:,}</td>'
            f'<td class="base">{g["median_ms"]:,.0f} ms</td><td class="win">{n["median_ms"]:,.0f} ms</td></tr>'
        )
    return (
        '<div class="table-scroll" tabindex="0" role="region" aria-labelledby="offline-title">'
        '<table class="bench"><thead>'
        '<tr><th rowspan="2" scope="col">Language</th><th rowspan="2" scope="col">Repo</th>'
        '<th colspan="2" scope="colgroup">Right file first</th>'
        '<th colspan="2" scope="colgroup">Tokens to the right file</th>'
        '<th colspan="2" scope="colgroup">Search time</th></tr>'
        '<tr><th scope="col" class="base">Grep</th><th scope="col">Nexus</th>'
        '<th scope="col" class="base">Grep</th><th scope="col">Nexus</th>'
        '<th scope="col" class="base">Grep</th><th scope="col">Nexus</th></tr>'
        "</thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def live_html(data: Dict) -> str:
    rows = []
    for r in live_repos(data):
        b, n = r["live"]["baseline"], r["live"]["nexus"]
        rows.append(
            f'<tr><th scope="row">{html.escape(r["language"])}</th>'
            f'<td class="repo">{_repo_link(r)}<span>build {html.escape(n["build"])}</span></td>'
            f'<td class="base">{b["median_tokens"]:,}</td>'
            + _cell(n["median_tokens"], b["median_tokens"],
                    f'{n["median_tokens"]:,}<small>{pct_change(n["median_tokens"], b["median_tokens"])}</small>',
                    EQUAL_BAND)
            + f'<td class="base">${b["cost_usd"]:.3f}</td>'
            + _cell(n["cost_usd"], b["cost_usd"],
                    f'${n["cost_usd"]:.3f}<small>{pct_change(n["cost_usd"], b["cost_usd"])}</small>',
                    EQUAL_BAND)
            + f'<td class="base">{_turns(b["turns"])}</td><td>{_turns(n["turns"])}</td>'
            f'<td class="base">{b["correct"]}/{b["of"]}</td><td>{n["correct"]}/{n["of"]}</td></tr>'
        )
    return (
        '<div class="table-scroll" tabindex="0" role="region" aria-labelledby="live-title">'
        '<table class="bench"><thead>'
        '<tr><th rowspan="2" scope="col">Language</th><th rowspan="2" scope="col">Repo</th>'
        '<th colspan="2" scope="colgroup">Tokens per task</th>'
        '<th colspan="2" scope="colgroup">Cost per task</th>'
        '<th colspan="2" scope="colgroup">Turns</th>'
        '<th colspan="2" scope="colgroup">Correct</th></tr>'
        '<tr>' + '<th scope="col" class="base">Grep</th><th scope="col">Nexus</th>' * 4 + "</tr>"
        "</thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def site_block(data: Dict) -> str:
    """The results section body of site/index.html."""
    h = headline(data)
    m = data["method"]
    note = not_yet_sentence(data)
    return f"""<div class="results-summary">
  <p><strong>{h['token_cut']:.0%}</strong> fewer tokens to reach the right file, median of {h['offline_repos']} repos ({h['token_cut_min']:.0%} to {h['token_cut_max']:.0%}).</p>
  <p><strong>{h['speedup']:.0f}×</strong> faster search than Grep and Read, median ({h['speedup_min']:.0f}× to {h['speedup_max']:.0f}×).</p>
  <p><strong>{h['cost_lower']} of {h['live_repos']}</strong> repos cost less per task in live Claude Code runs, with {h['correct_nexus']} of {h['tasks']} tasks correct against {h['correct_baseline']}.</p>
</div>
<h3 id="offline-title">Find the right file <span>offline check, no LLM</span></h3>
{offline_html(data)}
<h3 id="live-title">Answer the question <span>live Claude Code runs, {m['tasks_per_repo']} tasks × {m['runs_per_task']} runs, median per task</span></h3>
{live_html(data)}
<p class="results-note">{html.escape(note + ' ' if note else '')}Each row is the latest run of that repo. {html.escape(m['noise'])} <a href="benchmarks.html">Every number, the build of each row and the method</a>.</p>"""


def hero_block(data: Dict) -> str:
    """The hero trace: tokens to the right file per language, two lanes drawn to scale."""
    repos = offline_repos(data)
    tabs, panels = [], []
    for i, r in enumerate(repos):
        g, n = r["quick"]["grep"], r["quick"]["nexus"]
        key = re.sub(r"[^a-z0-9]+", "-", r["language"].lower()).strip("-") or f"l{i}"
        first = i == 0
        selected = "true" if first else "false"
        focus = "" if first else ' tabindex="-1"'
        hidden = "" if first else " hidden"
        tabs.append(
            f'<button role="tab" id="trace-tab-{key}" aria-controls="trace-{key}" '
            f'aria-selected="{selected}"{focus}>{html.escape(r["language"])}</button>'
        )
        width = max(n["median_tokens"] / g["median_tokens"] * 100, 0.8)

        def stats(s: Dict) -> str:
            return (
                f'<p class="lane-stats"><span><strong>{s["median_tokens"]:,}</strong> tokens</span>'
                f'<span>{s["median_ms"]:,.0f} ms</span>'
                f'<span>right file first {s["hit_at_1"]:.0%}</span></p>'
            )

        panels.append(
            f'<div class="trace-panel" role="tabpanel" id="trace-{key}" aria-labelledby="trace-tab-{key}"{hidden}>'
            f'<div class="lane lane-grep"><p class="lane-name">Grep and Read</p>'
            f'<div class="bar" style="--w:100%"></div>{stats(g)}</div>'
            f'<div class="lane lane-nexus"><p class="lane-name">Nexus-MCP</p>'
            f'<div class="bar" style="--w:{width:.1f}%"></div>{stats(n)}</div>'
            f'<p class="trace-source">{html.escape(r["project"])}, {r["files"]:,} files, {r["quick"]["questions"]} questions, median per question.</p>'
            "</div>"
        )
    h = headline(data)
    claim = (
        f'<p class="trace-claim"><strong>{h["token_cut"]:.0%} fewer tokens</strong> and '
        f'<strong>{h["speedup"]:.0f}× faster search</strong>, median of {h["offline_repos"]} '
        f"repos in {h['languages']} languages. Pick a language:</p>"
    )
    return (
        claim
        + '<div class="trace-tabs" role="tablist" aria-label="Language">' + "".join(tabs) + "</div>"
        + "".join(panels)
    )


# ---------------------------------------------------------------- llms.txt


def llms_block(data: Dict) -> str:
    m = data["method"]
    lines = [
        "## Benchmarks",
        "",
        f"Source of truth: {SITE_URL}/benchmarks.json (updated {data['updated']}, version "
        f"{data['version']}). One row per repo: the latest run.",
        "",
        f"- Summary: {summary_sentence(data)}",
    ]
    for r in offline_repos(data):
        g, n = r["quick"]["grep"], r["quick"]["nexus"]
        lines.append(
            f"- Offline check, {r['language']}, {r['project']} ({r['files']} files, build "
            f"{r['quick']['build']}): right file first {g['hit_at_1']:.0%} (Grep and Read) and "
            f"{n['hit_at_1']:.0%} (Nexus-MCP); median tokens to the right file "
            f"{g['median_tokens']} and {n['median_tokens']}; median search time "
            f"{g['median_ms']:.0f} ms and {n['median_ms']:.0f} ms."
        )
    for r in live_repos(data):
        b, n = r["live"]["baseline"], r["live"]["nexus"]
        lines.append(
            f"- Live run, {r['language']}, {r['project']} (build {n['build']}): median tokens "
            f"per task {b['median_tokens']} (Grep and Read) and {n['median_tokens']} (Nexus-MCP, "
            f"{ascii_pct(n['median_tokens'], b['median_tokens'])}); cost per task "
            f"${b['cost_usd']:.3f} and ${n['cost_usd']:.3f}; correct {b['correct']} and "
            f"{n['correct']} of {n['of']}."
        )
    for r in data["runs"]:
        b, n = r["baseline"], r["nexus"]
        lines.append(
            f"- {r['project']} ({r['files']} files, 12 questions, version 2.1.0): cost "
            f"${b['cost_usd']:.3f} with Grep and Read, ${n['cost_usd']:.3f} with Nexus-MCP "
            f"({ascii_pct(n['cost_usd'], b['cost_usd'])}); correct {b['correct']} and "
            f"{n['correct']} of {n['of']}."
        )
    for comp in data.get("comparisons", []):
        conds = "; ".join(
            f"{c['label']} {c['version']}: {c['median_tokens']} tokens, "
            f"${c['median_cost_usd']:.3f}, {c['correct']} of {c['of']} correct"
            for c in comp["conditions"]
        )
        lines.append(
            f"- {comp['title']}, {comp['project']} ({comp['language']}, {comp['tasks']} tasks, "
            f"{comp['runs_per_task']} runs each, medians per task): {conds}."
        )
    note = not_yet_sentence(data)
    if note:
        lines.append(f"- {note}")
    lines.append(f"- {m['noise']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- CSV

CSV_HEAD = [
    "repo", "language", "files", "pin", "tier", "condition", "build", "run",
    "median_tokens", "hit_at_1", "median_ms", "cost_usd", "turns", "wall_seconds",
    "correct", "of",
]  # fmt: skip


def csv_text(data: Dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_HEAD)

    def row(**cols) -> List:
        return ["" if cols.get(k) is None else cols[k] for k in CSV_HEAD]

    for r in data["repos"]:
        base = {"repo": r["project"], "language": r["language"], "files": r["files"], "pin": r["pin"]}
        q = r.get("quick")
        if q:
            for side, name in (("grep", "grep_and_read"), ("nexus", "nexus_mcp")):
                s = q[side]
                writer.writerow(row(**base, tier="offline", condition=name,
                                    build=q["build"] if side == "nexus" else "built-in tools",
                                    run=q["run"], median_tokens=s["median_tokens"],
                                    hit_at_1=s["hit_at_1"], median_ms=s["median_ms"]))
        live = r.get("live")
        if live:
            for side, name in (("baseline", "grep_and_read"), ("nexus", "nexus_mcp")):
                s = live[side]
                writer.writerow(row(**base, tier="live", condition=name, build=s["build"],
                                    run=s["run"], median_tokens=s["median_tokens"],
                                    cost_usd=s["cost_usd"], turns=s["turns"],
                                    wall_seconds=s["wall_seconds"], correct=s["correct"],
                                    of=s["of"]))
    for r in data["runs"]:
        for side, name in (("baseline", "grep_and_read"), ("nexus", "nexus_mcp")):
            s = r[side]
            writer.writerow(row(repo=r["project"], files=r["files"], pin=r["commit"],
                                tier="live-12q", condition=name, run=r["id"],
                                cost_usd=s["cost_usd"], turns=s["turns"],
                                correct=s["correct"], of=s["of"]))
    return out.getvalue()


# ---------------------------------------------------------------- benchmarks.html


def comparison_md(comp: Dict) -> List[str]:
    """A head-to-head comparison of tools as Markdown lines: one row per condition."""
    name = f"[{comp['project']}]({comp['url']})" if comp.get("url") else comp["project"]
    lines = [
        f"{comp['title']} on {name} ({comp['language']}, {comp['files']:,} files): "
        f"{comp['tasks']} tasks, {comp['runs_per_task']} runs each, {comp['date']}.",
        "",
        "| Condition | Version | Tokens (median) | Cost per task (median) | Correct |",
        "|---|---|---:|---:|---:|",
    ]
    for c in comp["conditions"]:
        lines.append(
            f"| {c['label']} | {c['version']} | {c['median_tokens']:,} | "
            f"${c['median_cost_usd']:.3f} | {c['correct']} of {c['of']} |"
        )
    return lines


def load_community() -> List[Dict]:
    """Shared results that a maintainer accepted (see scripts/ingest_community_result.py)."""
    return json.loads(COMMUNITY.read_text()) if COMMUNITY.exists() else []


def community_html(entries: List[Dict]) -> str:
    """The community table. Values were validated on ingest; they are escaped again here."""
    head = (
        '<section class="page-section"><h2>Community results</h2>\n<p>Runs shared with '
        "<code>nexus-mcp-ci eval share</code> and accepted by a maintainer. Numbers only: no "
        "code and no private names.</p>\n"
    )
    if not entries:
        return head + "<p>None yet. Yours can be the first.</p></section>\n"
    rows = []
    for e in entries:
        quick = e.get("quick")
        if quick:
            g, n = quick["grep"]["median_tokens"], quick["nexus"]["median_tokens"]
            detail = f"{html.escape(quick['verdict'])}; tokens {g:,.0f} to {n:,.0f}"
        else:
            rec = e.get("agent", {}).get("recommendation", {})
            detail = f"best: {html.escape(str(rec.get('spec')))} {html.escape(str(rec.get('condition')))}"
        src = e["source"]
        project = (
            f'<a href="{html.escape(src["url"])}">{html.escape(src["url"].split("github.com/")[1])}</a>'
            if src.get("url")
            else html.escape(src["kind"])
        )
        rows.append(
            f"<tr><td>#{int(e['issue'])}</td><td>{html.escape(e['created'])}</td>"
            f"<td>{html.escape(e['tier'])}</td><td>{project}</td>"
            f"<td>{html.escape(e['repo']['size_band'])}</td>"
            f"<td>{html.escape(', '.join(e['repo']['languages']))}</td><td>{detail}</td>"
            f"<td>{html.escape(e['nexus_mcp_ci'])}</td></tr>"
        )
    return (
        head + '<div class="table-scroll" tabindex="0"><table class="plain"><thead><tr><th>Issue</th>'
        "<th>Date</th><th>Tier</th><th>Project</th><th>Size</th><th>Languages</th><th>Result</th>"
        "<th>Version</th></tr></thead><tbody>\n" + "\n".join(rows) + "\n</tbody></table></div></section>\n"
    )


def comparison_html(comp: Dict) -> str:
    """A head-to-head comparison section: totals per condition, then each task."""
    conds = comp["conditions"]
    name = html.escape(comp["project"])
    if comp.get("url"):
        name = f'<a href="{html.escape(comp["url"])}">{name}</a>'
    summary = "".join(
        f"<tr><td>{html.escape(c['label'])}</td><td>{html.escape(c['version'])}</td>"
        f"<td>{c['median_tokens']:,}</td><td>${c['median_cost_usd']:.3f}</td>"
        f"<td>${c['total_cost_usd']:.2f}</td><td>{c['median_turns']:g}</td>"
        f"<td>{c['correct']} of {c['of']}</td></tr>"
        for c in conds
    )
    heads = "".join(f"<th>{html.escape(c['label'])}</th>" for c in conds)
    tasks = "".join(
        f"<tr><td><code>{html.escape(t['task'])}</code></td><td>{html.escape(t['category'])}</td>"
        + "".join(
            f"<td>{t['tokens'][c['key']]:,}, ${t['cost_usd'][c['key']]:.3f}</td>" for c in conds
        )
        + "</tr>"
        for t in comp["per_task"]
    )
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in comp["notes"])
    fairness = "".join(f"<li>{html.escape(n)}</li>" for n in comp.get("fairness", []))
    return f"""<section class="page-section"><h2>Head to head: {html.escape(comp["title"])}</h2>
<p>{name} ({html.escape(comp["language"])}, {comp["files"]:,} files, commit <code>{html.escape(comp["commit"])}</code>): {comp["tasks"]} tasks, {comp["runs_per_task"]} runs each, {html.escape(comp["agent"])}, {html.escape(comp["date"])}. Each total is {html.escape(comp["aggregate"])}.</p>
<div class="table-scroll" tabindex="0"><table class="plain"><thead><tr><th>Condition</th><th>Version</th><th>Tokens (median)</th><th>Cost per task (median)</th><th>Cost of all runs</th><th>Turns</th><th>Correct</th></tr></thead><tbody>{summary}</tbody></table></div>
<div class="table-scroll" tabindex="0"><table class="plain"><thead><tr><th>Task</th><th>Category</th>{heads}</tr></thead><tbody>{tasks}</tbody></table></div>
<ul>{notes}</ul>
<h3>How the comparison was kept fair</h3>
<ul>{fairness}</ul>
<p>The tasks are in <code>{html.escape(comp["task_suite"])}</code>.</p></section>
"""


def detail_html(data: Dict) -> str:
    """Every column of each repo, for the benchmarks page."""
    rows = []
    for r in data["repos"]:
        q, live = r.get("quick"), r.get("live")
        pin = f'<code>{html.escape(str(r["pin"])[:7])}</code>'
        off = (
            f"<td>{html.escape(q['build'])}</td><td>{q['date']}</td><td>{q['index_seconds']:,.0f} s</td>"
            f"<td>{q['grep']['hit_at_3']:.0%} / {q['nexus']['hit_at_3']:.0%}</td>"
            f"<td>{html.escape(q['verdict'])}</td>"
            if q else "<td>–</td>" * 5
        )
        if live:
            b, n = live["baseline"], live["nexus"]
            wall = (
                f"{b['wall_seconds']:g} / {n['wall_seconds']:g} s"
                if b.get("wall_seconds") and n.get("wall_seconds") else "–"
            )
            lv = (
                f"<td>{html.escape(n['build'])}</td><td>{html.escape(b['run'].removeprefix('languages-'))} / "
                f"{html.escape(n['run'].removeprefix('languages-'))}</td><td>{wall}</td>"
            )
        else:
            lv = "<td>–</td>" * 3
        hit = r.get("retrieval_hit_at_1")
        cpf = r.get("calls_per_fn")
        rows.append(
            f'<tr><th scope="row">{html.escape(r["language"])}</th><td class="repo">{_repo_link(r)}'
            f"<span>{pin}</span></td>{off}"
            f"<td>{'–' if hit is None else f'{hit:.2f}'}</td><td>{'–' if cpf is None else f'{cpf:.2f}'}</td>{lv}</tr>"
        )
    return (
        '<div class="table-scroll" tabindex="0"><table class="plain detail"><thead><tr>'
        "<th>Language</th><th>Repo, pin</th><th>Offline build</th><th>Date</th><th>Index time</th>"
        "<th>Right file in top 3 (Grep / Nexus)</th><th>Verdict</th><th>Search hit@1</th>"
        "<th>Calls per function</th><th>Live build</th><th>Runs (Grep / Nexus)</th>"
        "<th>Wall time per task (Grep / Nexus)</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def other_html(data: Dict) -> str:
    runs = "".join(
        f"<tr><td>{html.escape(r['project'])}</td><td>{r['files']:,}</td>"
        f"<td>${r['baseline']['cost_usd']:.3f} / ${r['nexus']['cost_usd']:.3f} "
        f"({pct_change(r['nexus']['cost_usd'], r['baseline']['cost_usd'])})</td>"
        f"<td>{r['baseline']['turns']} / {r['nexus']['turns']}</td>"
        f"<td>{r['baseline']['correct']} / {r['nexus']['correct']} of {r['nexus']['of']}</td>"
        f"<td><code>{html.escape(r['commit'])}</code>, {r['date']}</td></tr>"
        for r in data["runs"]
    )
    def project(q: Dict) -> str:
        name = html.escape(q["project"])
        return f'<a href="{html.escape(q["url"])}">{name}</a>' if q.get("url") else name

    quick = "".join(
        f"<tr><td>{project(q)}</td>"
        f"<td>{q['files']:,}</td><td>{html.escape(q['verdict'])}</td>"
        f"<td>{q['grep']['median_tokens']:,} / {q['nexus']['median_tokens']:,}</td>"
        f"<td>{q['grep']['hit_at_1']:.0%} / {q['nexus']['hit_at_1']:.0%}</td><td>{q['date']}</td></tr>"
        for q in data["quick_tier"]
    )
    suites = "".join(
        f"<tr><td><code>{html.escape(k)}</code></td><td>{v:.2f}</td></tr>"
        for k, v in data["retrieval"]["suites"].items()
    )
    return f"""<section class="page-section"><h2>Other runs</h2>
<p>These projects have one run each, so they keep it.</p>
<h3>Live, 12 questions per project (version 2.1.0)</h3>
<div class="table-scroll" tabindex="0"><table class="plain"><thead><tr><th>Project</th><th>Files</th><th>Cost (Grep / Nexus)</th><th>Turns</th><th>Correct</th><th>Commit, date</th></tr></thead><tbody>{runs}</tbody></table></div>
<h3>Offline check on other projects</h3>
<div class="table-scroll" tabindex="0"><table class="plain"><thead><tr><th>Project</th><th>Files</th><th>Verdict</th><th>Tokens to the right file (Grep / Nexus)</th><th>Right file first</th><th>Date</th></tr></thead><tbody>{quick}</tbody></table></div>
<h3>Search accuracy gate ({html.escape(data["retrieval"]["metric"])})</h3>
<p>A change ships only when these do not fall.</p>
<div class="table-scroll" tabindex="0"><table class="plain"><thead><tr><th>Retrieval suite</th><th>{html.escape(data["retrieval"]["metric"])}</th></tr></thead><tbody>{suites}</tbody></table></div></section>
"""


def page_html(data: Dict, community: Optional[List[Dict]] = None) -> str:
    community = load_community() if community is None else community
    m = data["method"]
    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Nexus-MCP token-efficiency benchmark",
        "description": "Tokens, search time, cost and correct answers of coding agents with "
        "Nexus-MCP against Grep and Read, on open-source repos in 10 languages.",
        "url": f"{SITE_URL}/benchmarks.html",
        "dateModified": data["updated"],
        "license": f"{REPO_URL}/blob/main/LICENSE",
        "creator": {"@type": "Person", "name": "Shreyas Jagannath"},
        "distribution": [
            {"@type": "DataDownload", "encodingFormat": "application/json",
             "contentUrl": f"{SITE_URL}/benchmarks.json"},
            {"@type": "DataDownload", "encodingFormat": "text/csv",
             "contentUrl": f"{SITE_URL}/benchmarks.csv"},
        ],
    }  # fmt: skip
    method = "".join(
        f"<li>{html.escape(t)}</li>"
        for t in (
            f"Live: {m['agent']}. {m['tasks_per_repo']} tasks per repo, {m['runs_per_task']} runs each; each number is {m['aggregate']}.",
            f"Baseline: {m['baseline']}. Nexus-MCP: {m['nexus']}.",
            f"Offline: {m['offline']}.",
            m["latest_rule"],
            m["noise"],
        )
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nexus-MCP Benchmarks</title>
<meta name="description" content="Tokens, search time, cost and accuracy of coding agents with Nexus-MCP against Grep and Read, on 12 open-source repos in 10 languages. Method and raw data.">
<link rel="canonical" href="{SITE_URL}/benchmarks.html">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="styles.css">
<script type="application/ld+json">{json.dumps(dataset)}</script>
</head>
<body class="page">
<header class="nav"><div class="wrap nav-inner"><a class="brand" href="./"><img src="favicon.svg" width="24" height="24" alt=""><span>Nexus-MCP</span></a><nav class="nav-links" aria-label="Primary"><a href="index.html#results">Results</a><a href="index.html#install">Install</a><a href="{REPO_URL}" rel="noopener">GitHub</a></nav></div></header>
<main id="main" class="wrap page-main">
<h1>Benchmarks</h1>
<p class="lede">{html.escape(summary_sentence(data))}</p>
<p>Updated {data["updated"]}, Nexus-MCP {data["version"]}. Each row is the latest run of that repo. Grep and Read is the baseline: the agent's own search and file reads.</p>
<section class="page-section results-body">
{site_block(data)}
</section>
<section class="page-section"><h2>Every column</h2>
<p>The build that made each row, the index time, the top 3 hit rate, the search accuracy, the call-graph density and the wall time. Wall time per task is about equal: Nexus-MCP saves turns, but each of its calls returns more code.</p>
{detail_html(data)}</section>
{other_html(data)}{"".join(comparison_html(c) for c in data.get("comparisons", []))}{community_html(community)}<section class="page-section"><h2>Method</h2>
<ul>{method}</ul>
<p>Run it on your own code: <code>uvx --python 3.12 nexus-mcp-ci eval</code> (offline, free) or <code>nexus-mcp-ci eval --agent claude</code> (a paid A/B with your own Claude Code login; it shows the cost first). <a href="{html.escape(m["source"])}">Full method</a>, <a href="{html.escape(m["runs"])}">raw runs</a>.</p>
<p>Data: <a href="benchmarks.json">benchmarks.json</a> (source of truth) and <a href="benchmarks.csv">benchmarks.csv</a>.</p></section>
</main>
<footer class="footer"><div class="wrap footer-inner"><p>Nexus-MCP, MIT licence</p><p><a href="./">Home</a> <a href="{REPO_URL}" rel="noopener">GitHub</a> <a href="https://pypi.org/project/nexus-mcp-ci/" rel="noopener">PyPI</a></p></div></footer>
<script src="main.js" defer></script>
</body>
</html>
"""


# ---------------------------------------------------------------- files


def replace_block(text: str, block: str, path: Path, name: str = "results") -> str:
    start, end = markers(name)
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"{path}: markers {start} ... {end} not found")
    # Keep the indent of the start marker, so the end marker lines up with it.
    line_start = text.rfind("\n", 0, match.start()) + 1
    indent = text[line_start : match.start()]
    indent = indent if not indent.strip() else ""
    return pattern.sub(lambda _: f"{start}\n{block}\n{indent}{end}", text, count=1)


def outputs(data: Dict) -> Dict[Path, str]:
    files = {
        ROOT / "site" / "benchmarks.json": json.dumps(data, indent=2) + "\n",
        ROOT / "site" / "benchmarks.csv": csv_text(data),
        ROOT / "site" / "benchmarks.html": page_html(data),
    }
    blocks = {
        "README.md": [("results", readme_block(data))],
        "site/index.html": [("hero", hero_block(data)), ("results", site_block(data))],
        "llms.txt": [("results", llms_block(data))],
    }
    for rel, parts in blocks.items():
        path = ROOT / rel
        text = path.read_text()
        for name, block in parts:
            text = replace_block(text, block, path, name)
        files[path] = text
    return files


def _show(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Fail when output is out of date")
    args = parser.parse_args(argv)
    data = json.loads(DATA.read_text())
    if data.get("schema_version") not in SCHEMA_VERSIONS:
        raise SystemExit(f"{_show(DATA)}: schema_version must be one of {SCHEMA_VERSIONS}")
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
