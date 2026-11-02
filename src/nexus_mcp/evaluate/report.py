"""Render the eval result as Markdown, with the data-flow block that every report ends with."""

from typing import Any, Dict


def data_block(record: Dict[str, Any], store_path: str) -> str:
    """What this run read, wrote and sent. Always shown, so nobody has to guess."""
    source = record["source"]
    read = {
        "path": f"Your project files in {source['label']}",
        "url": f"A shallow clone of {source['label']}",
        "example": "The bundled example project",
    }[source["kind"]]
    sent = ["Nothing. This run used no LLM and made no network call of its own."]
    if source["kind"] == "url":
        sent = [f"A `git clone` of {source['label']} (network, by git)."]
    if record.get("kept_index"):
        index_note = "the index in your project's `.nexus/` folder"
    else:
        index_note = "a temporary index that was deleted at exit"
    return "\n".join(
        [
            "## Data in this run",
            f"- Read: {read}.",
            f"- Written: this report in `{store_path}` and {index_note}",
            f"- Sent: {sent[0]}",
            "- Network: the one-time download of the embedding model, if it was not cached.",
            "- Remove it all: `nexus-mcp-ci data where`, then "
            "`nexus-mcp-ci data clear --evals --yes`.",
        ]
    )


def _pct(value: float) -> str:
    return f"{value:.0%}"


def render(record: Dict[str, Any], store_path: str) -> str:
    verdict = record["verdict"]
    repo = record["repo"]
    grep, nexus = record["summary"]["grep"], record["summary"]["nexus"]
    lines = [
        f"# Nexus-MCP eval: {verdict['label']}",
        "",
        f"Project: `{record['source']['label']}` · {repo['files']} files · "
        f"{', '.join(repo['languages']) or 'unknown languages'} · "
        f"{record['questions']} questions · index built in {repo['index_seconds']}s",
        "",
        *[f"- {reason}" for reason in verdict["reasons"]],
        "",
        "| | Grep and Read | Nexus-MCP |",
        "|---|---:|---:|",
        f"| Right file first | {_pct(grep['hit_at_1'])} | {_pct(nexus['hit_at_1'])} |",
        f"| Right file in top 3 | {_pct(grep['hit_at_3'])} | {_pct(nexus['hit_at_3'])} |",
        f"| Median tokens to answer | {grep['median_tokens']:.0f} | {nexus['median_tokens']:.0f} |",
        f"| Median latency (ms) | {grep['median_ms']:.0f} | {nexus['median_ms']:.0f} |",
        "",
        "### By kind of question",
        "| Kind | Questions | Top 3: grep | Top 3: nexus | Tokens: grep | Tokens: nexus |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for kind, row in record["by_kind"].items():
        g, n = row["grep"], row["nexus"]
        lines.append(
            f"| {kind} | {g['questions']} | {_pct(g['hit_at_3'])} | {_pct(n['hit_at_3'])} | "
            f"{g['median_tokens']:.0f} | {n['median_tokens']:.0f} |"
        )
    lines += [
        "",
        "These numbers come from a token proxy, not from a live agent: they count the text "
        "an agent must read to reach the right file. They show direction, not a bill. "
        "The live benchmark in `benchmarks/` measures real agent cost.",
        "",
        "## Next step",
        "- Fit looks good? Install: `claude mcp add nexus-mcp -- nexus-mcp-ci` "
        "(Claude Code plugin: `/plugin install nexus-mcp@nexus-mcp`).",
        "- Weak fit? Keep Grep and Read for this project. That is a valid result.",
        "",
        data_block(record, store_path),
    ]
    return "\n".join(lines) + "\n"
